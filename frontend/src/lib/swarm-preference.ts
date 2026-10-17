/**
 * Persistence for the composer's swarm mode.
 *
 * Mirrors `delegation-preference.ts`: the mode is an opt-in that spends tokens
 * and starts workers, so it defaults to **off** and is never sent as a literal
 * `false`.  The difference from delegation is that a swarm also carries a
 * *topology* (`auto`/`parallel`/`map_reduce`/...) and a *concurrency* ceiling,
 * so the preference is a small object rather than a single string.
 *
 * The three fields are reconciled independently against what the current
 * catalog actually offers, for the same reason as delegation: a deployment that
 * renames or removes a mode (or lowers the concurrency ceiling) must not come
 * back with the selector parked on a choice that resolves to nothing.
 */

export const SWARM_MODE_STORAGE_KEY = "alpha_swarm_mode";
export const SWARM_MODE_DEFAULT = "off" as const;

export type SwarmModePreference = {
  enabled: boolean;
  mode: string;
  concurrency: number;
};

export type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const ALWAYS_VALID_MODES = new Set(["auto", "parallel", "map_reduce", "scatter_gather", "hierarchical", "debate", "ensemble", "coding_worktree", "read_fanout", "critical_path_split"]);

/** The modes the Gateway actually mounts (mirrors `SWARM_MODES` in TeamOps). */
export const KNOWN_SWARM_MODES: readonly string[] = [
  "auto", "parallel", "map_reduce", "scatter_gather", "hierarchical",
  "debate", "ensemble", "coding_worktree",
  "read_fanout", "critical_path_split",
];

/** Mode + human label, for the composer selector and the Team Ops surface. */
export const SWARM_MODES: readonly [string, string][] = [
  ["auto", "Auto — route from the goal and measured task graph"],
  ["parallel", "Parallel — independent tasks run together"],
  ["map_reduce", "Map→Reduce — split inputs, then combine results"],
  ["scatter_gather", "Scatter→Gather — broad exploration with a collection phase"],
  ["hierarchical", "Hierarchical — delegate planning through task levels"],
  ["debate", "Debate — independent positions challenge one another"],
  ["ensemble", "Ensemble — independent solutions feed a synthesis"],
  ["coding_worktree", "Coding Worktree - isolate coding workers in worktrees"],
  ["read_fanout", "Read Fanout - N read-only workers, safe to scale wide"],
  ["critical_path_split", "Critical Path Split - balance the longest branch"],
];

export function isSwarmModeShape(value: unknown): value is string {
  return typeof value === "string" && ALWAYS_VALID_MODES.has(value);
}

export function defaultStorage(): StorageLike | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}

export function readStoredSwarmPreference(storage: StorageLike | null | undefined): SwarmModePreference | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(SWARM_MODE_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SwarmModePreference>;
    if (typeof parsed !== "object" || parsed === null) return null;
    return {
      enabled: parsed.enabled === true,
      mode: isSwarmModeShape(parsed.mode) ? (parsed.mode as string) : "auto",
      concurrency: typeof parsed.concurrency === "number" && parsed.concurrency >= 1 && parsed.concurrency <= 64
        ? Math.floor(parsed.concurrency)
        : 4,
    };
  } catch {
    return null;
  }
}

export function storeSwarmPreference(storage: StorageLike | null | undefined, pref: SwarmModePreference): boolean {
  if (!storage) return false;
  try {
    if (!pref.enabled && pref.mode === "auto" && pref.concurrency === 4) {
      storage.removeItem(SWARM_MODE_STORAGE_KEY);
    } else {
      storage.setItem(SWARM_MODE_STORAGE_KEY, JSON.stringify(pref));
    }
    return true;
  } catch {
    return false;
  }
}

/** Whether a stored mode survives against the modes the current build offers. */
export function reconcileSwarmMode(stored: unknown, available: Iterable<string>): { mode: string; restored: boolean } {
  if (!isSwarmModeShape(stored)) return { mode: "auto", restored: false };
  const set = new Set(available);
  if (set.size === 0) return { mode: stored, restored: true };
  return set.has(stored) ? { mode: stored, restored: true } : { mode: "auto", restored: false };
}