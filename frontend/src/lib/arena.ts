import { get, send } from "./http";

export type ArenaMode = "decide" | "plan";
export type ArenaRun = {
  run_id: string;
  status: string;
  profile?: string;
  mode?: string;
  agents_n?: number;
  task?: string;
};

export async function listArenaRuns(): Promise<ArenaRun[]> {
  const value = await get<unknown>("/arena/runs");
  if (!Array.isArray(value)) throw new Error("Arena runs response was not a list.");
  return value.filter((row): row is ArenaRun => Boolean(row && typeof row === "object" && typeof (row as ArenaRun).run_id === "string"));
}

export async function createArenaRun(task: string, options: { mode: ArenaMode; profile: string; agents: number }): Promise<ArenaRun> {
  const payload: Record<string, unknown> = {
    task,
    mode: options.mode,
    agents: options.agents,
  };
  if (options.profile !== "custom") payload.profile = options.profile;
  return send<ArenaRun>("/arena/runs", "POST", payload);
}

export async function startArenaRun(runId: string, confirm = false): Promise<{ run_id: string; status: string }> {
  return send(`/arena/runs/${encodeURIComponent(runId)}/start`, "POST", { confirm }, 120_000);
}

