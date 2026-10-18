/**
 * The chat-side swarm run board's projection.
 *
 * When the lead agent (or the operator) starts a *coordinator swarm*, the run
 * executes in the Gateway's swarm runtime — not as in-turn delegation — so no
 * `task_*` stream event exists for it. This module decides which of the
 * operator's swarms belong on that board and carries nothing about lifecycle
 * control: mutating actions stay owned by Team Ops (`TeamOpsSection`).
 *
 * Every figure travels from the already-mapped `Swarm` rows (`lib/teamops.ts`)
 * — the single projection of the list endpoint. This module re-maps nothing:
 * it only answers "what is running right now", and labels progress through the
 * same `swarmProgress`/`swarmProgressLabel` pair the Team Ops DAG uses.
 */

import { isRestartRecoverable, isTerminalSwarmStatus, swarmProgress, swarmProgressLabel, type Swarm } from "@/lib/teamops";

export interface SwarmRunRow {
  id: string;
  objective: string;
  /** Gateway status, verbatim (unknown statuses are shown honestly upstream). */
  status: string;
  paused: boolean;
  /** The same measured progress language the Team Ops DAG prints. */
  progressLabel: string;
  /** Requested topology, verbatim; empty string when the row did not report one. */
  topology: string;
  requestedConcurrency: string;
}

/**
 * The rows the chat board shows: *only* swarms whose execution has not reached
 * a final verdict yet. A finished swarm disappears from chat on purpose — the
 * chat board answers "what is running right now", and the history (and its
 * lifecycle controls) stay in Team Ops.
 */
export function swarmRunRows(swarms: Swarm[]): SwarmRunRow[] {
  return swarms
    .filter((swarm) => !isTerminalSwarmStatus(swarm.status))
    .map((swarm) => ({
      id: swarm.id,
      objective: swarm.objective,
      status: swarm.status,
      paused: swarm.status === "paused",
      progressLabel: swarmProgressLabel(swarmProgress(swarm.progress)),
      topology: swarm.mode ?? "",
      requestedConcurrency:
        typeof swarm.maxConcurrency === "number" && swarm.maxConcurrency > 0
          ? `ceiling ${swarm.maxConcurrency}`
          : "auto-sized",
    }));
}

export interface SwarmRecoveryBanner {
  /** How many loaded rows are restart-recovered. A null banner means zero. */
  count: number;
  /** The server's own `terminal_reason`, verbatim — never reworded, never guessed. */
  terminalReason: string;
}

/**
 * The recovered-plan banner for the chat board (plan 3.1). The board stays a
 * read-only monitor — lifecycle controls are owned by Team Ops — so this is a
 * display projection only: it names how many plans a restart parked and the
 * server's exact reason, so the operator knows why a swarm is paused and where
 * to Resume or Abandon it. A pause without that reason never produces a banner.
 */
export function swarmRecoveryBanner(swarms: Swarm[]): SwarmRecoveryBanner | null {
  const recovered = swarms.filter(isRestartRecoverable);
  if (recovered.length === 0) return null;
  return {
    count: recovered.length,
    terminalReason: recovered[0].terminalReason ?? "",
  };
}
