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

// ---------------------------------------------------------------------------
// The operator's ceilings
// ---------------------------------------------------------------------------

/**
 * One field of `GET /arena/config`.
 *
 * Every numeric field is `number | null` on purpose. These are the Gateway's
 * own ceilings, and a read that did not answer must render as "not reported" -
 * never as `0`, which would read as "you may run zero agents" instead of
 * "the Gateway did not say".
 */
export interface ArenaCeiling {
  enabled: boolean | null;
  defaultAgents: number | null;
  maxAgents: number | null;
  defaultWave: number | null;
  maxWave: number | null;
  estimateTokensPerCall: number | null;
  confirmationOverCalls: number | null;
  maxCallsPerRun: number | null;
  maxTokensPerRun: number | null;
  maxWallSecondsPerRun: number | null;
  maxJudgeAttempts: number | null;
  reasoningBankSeed: boolean | null;
}

const num = (row: Record<string, unknown>, key: string): number | null =>
  typeof row[key] === "number" ? (row[key] as number) : null;
const bool = (row: Record<string, unknown>, key: string): boolean | null =>
  typeof row[key] === "boolean" ? (row[key] as boolean) : null;

export async function getArenaConfig(): Promise<ArenaCeiling> {
  const value = await get<Record<string, unknown>>("/arena/config");
  if (!value || typeof value !== "object") throw new Error("Arena config response was not an object.");
  return {
    enabled: bool(value, "enabled"),
    defaultAgents: num(value, "default_agents"),
    maxAgents: num(value, "max_agents"),
    defaultWave: num(value, "default_wave"),
    maxWave: num(value, "max_wave"),
    estimateTokensPerCall: num(value, "estimate_tokens_per_call"),
    confirmationOverCalls: num(value, "require_confirmation_over_calls"),
    maxCallsPerRun: num(value, "max_subagent_calls_per_run"),
    maxTokensPerRun: num(value, "max_tokens_per_run"),
    maxWallSecondsPerRun: num(value, "max_wall_seconds_per_run"),
    maxJudgeAttempts: num(value, "max_judge_attempts"),
    reasoningBankSeed: bool(value, "reasoning_bank_seed"),
  };
}

/** One competitor profile, as the Gateway describes it. */
export interface ArenaProfile {
  name: string;
  agents: number | null;
  wave: number | null;
  description: string | null;
}

/** One Arena mode, as the Gateway describes it. */
export interface ArenaModeInfo {
  mode: string;
  description: string | null;
  /**
   * Whether this build actually implements the mode. The Gateway reports this
   * separately from the mode list, so a mode that is advertised but not
   * implemented is shown as such rather than being offered as if it ran.
   */
  implemented: boolean | null;
}

export interface ArenaCatalogue {
  profiles: ArenaProfile[];
  modes: ArenaModeInfo[];
}

/**
 * `GET /arena/profiles` - the profiles and modes the Gateway offers.
 *
 * The `modes` array is preserved verbatim: an unrecognised mode string from a
 * newer Gateway is displayed rather than snapped to a known value, and a mode
 * missing from `implemented_modes` is reported as unimplemented instead of
 * being quietly presented as usable.
 */
export async function getArenaCatalogue(): Promise<ArenaCatalogue> {
  const value = await get<Record<string, unknown>>("/arena/profiles");
  if (!value || typeof value !== "object") throw new Error("Arena profiles response was not an object.");
  const profiles = Array.isArray(value.profiles) ? value.profiles : [];
  const modes = Array.isArray(value.modes) ? value.modes : [];
  const implemented = Array.isArray(value.implemented_modes) ? (value.implemented_modes as unknown[]) : null;
  return {
    profiles: profiles
      .filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === "object")
      .map((row) => ({
        name: typeof row.profile === "string" ? row.profile : "",
        agents: num(row, "agents"),
        wave: num(row, "wave"),
        description: typeof row.description === "string" ? row.description : null,
      }))
      .filter((row) => row.name !== ""),
    modes: modes
      .filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === "object")
      .map((row) => ({
        mode: typeof row.mode === "string" ? row.mode : "",
        description: typeof row.description === "string" ? row.description : null,
        // `null` means the Gateway did not report an implementation list at
        // all, which is not the same as "none of them are implemented".
        implemented: implemented === null ? null : implemented.includes(row.mode as string),
      }))
      .filter((row) => row.mode !== ""),
  };
}

