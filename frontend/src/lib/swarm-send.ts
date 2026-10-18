/**
 * The composer's send decision, as a pure function.
 *
 * The composer's swarm toggle ("Send as swarm") is **dispatch**, not advice:
 * when it is on, the prompt is spent as a coordinator swarm
 * (`POST /api/swarms` then `POST /api/swarms/{id}/run-async`) and the regular
 * `/runs/stream` admission is never issued. The two branches are mutually
 * exclusive so the same prompt can never be double-spent — once as a streamed
 * run and once as a swarm. This module encodes that decision with no React and
 * no network, so the branch is unit-testable and the component's submit path
 * stays a thin interpreter of it.
 *
 * Server contract (backend/app/gateway/routers/swarms.py):
 *   POST /api/swarms                body: SwarmCreateRequest -> plan + cost_projection
 *   POST /api/swarms/{id}/run-async body: {} -> { status: "started_async", ... }
 */

/** The inputs the composer owns when it decides how to spend a prompt. */
export interface SwarmSendInput {
  /** The swarm toggle. When true the prompt is dispatched as a swarm. */
  swarmModeEnabled: boolean;
  /** The prompt text, sent verbatim as the swarm `goal`. */
  content: string;
  /** Selected topology (`auto`, `parallel`, ...); forwarded as `mode`. */
  swarmMode: string;
  /** Requested worker ceiling; forwarded as `max_concurrency`. */
  swarmConcurrency: number;
  /** The assistant the non-swarm branch would run (`lead_agent` or a bot). */
  assistantId: string;
  /** The model selection for the non-swarm branch. */
  modelName: string | undefined;
}

/** `POST /api/swarms` body — the swarm-create branch's first request. */
export interface SwarmCreateRequest {
  goal: string;
  mode: string;
  max_concurrency: number;
}

/** `POST /api/swarms/{id}/run-async` body — empty by the route's contract. */
export const SWARM_RUN_ASYNC_BODY = {} as const;

/**
 * The exclusive send decision. Exactly one branch is ever populated:
 *
 * - swarm on  -> `{ kind: "swarm", create, runAsyncPath }`
 * - swarm off -> `{ kind: "run", runPath, assistantId, modelName }`
 *
 * The two shapes share no fields that could carry the other branch's request,
 * so a caller physically cannot send both for one prompt.
 */
export type SwarmSendRequest =
  | {
      kind: "swarm";
      /** Body for `POST /api/swarms`. */
      create: SwarmCreateRequest;
      /** Path for `POST /api/swarms/{id}/run-async` once the create returns an id. */
      runAsyncPath: (swarmId: string) => string;
    }
  | {
      kind: "run";
      /** Path for the streaming admission `POST /threads/{id}/runs/stream`. */
      runPath: string;
      assistantId: string;
      modelName: string | undefined;
    };

export function buildSwarmSendRequest(input: SwarmSendInput): SwarmSendRequest {
  if (input.swarmModeEnabled) {
    return {
      kind: "swarm",
      create: {
        goal: input.content,
        mode: input.swarmMode,
        max_concurrency: input.swarmConcurrency,
      },
      runAsyncPath: (swarmId: string) =>
        `/swarms/${encodeURIComponent(swarmId)}/run-async`,
    };
  }
  return {
    kind: "run",
    runPath: "",
    assistantId: input.assistantId,
    modelName: input.modelName,
  };
}
