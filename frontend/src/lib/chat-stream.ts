import { apiFetch, ApiClientError } from "./api-client";
import { createSseDecoder, createSseState, reduceSse, runIdFromLocation, streamMessages, streamTasks, streamTodos, StreamMessage, SubagentTask, ReplayGapEvent, SseErrorDetail, TodoPlan } from "./sse-reducer";

/**
 * A stream that ended because the run failed, carrying the reason the Gateway
 * sent with it.
 *
 * It is an `ApiClientError` with `kind: "response"` so every existing caller,
 * and every existing assertion on `error.kind`, keeps working unchanged — a
 * dedicated error *type* rather than a new `ApiFailureKind`, because the
 * transport did not misbehave: the run reported a failure and the failure is
 * the news. Before this existed the reducer parsed `code`/`correlation_id` off
 * the `event: error` frame and every throw site dropped it on the floor.
 */
export class StreamRunFailure extends ApiClientError {
  /** The Gateway's own account of the failure, already bounded by the reducer. */
  readonly sseError: SseErrorDetail | null;

  constructor(sseError: SseErrorDetail | null = null) {
    // `detail` stays null: the server's failure *message* is untrusted content
    // and must not become the thrown message. `chatSupportId` is the only
    // sanctioned consumer of the identity fields.
    super("response");
    this.name = "StreamRunFailure";
    this.sseError = sseError;
  }
}

/**
 * Client-side reconnect ladder, used ONLY when the server sent no `retry:`
 * delay.
 *
 * `retryDelay` starts at `0`, so before this existed a stream that dropped
 * without a `retry:` frame was re-dialed with **no wait at all** — three
 * immediate reconnects against whatever had just failed to deliver a frame.
 * That is the worst case for the dependency: a backend mid-restart or a proxy
 * with a full accept queue gets hammered precisely when it is least able to
 * answer, and each attempt costs the same as a real one.
 *
 * A server-supplied delay always wins and is used verbatim — the server knows
 * whether it is shedding load, and the existing contract (and its tests) pin
 * that a `retry:` frame is honoured exactly. This ladder is the fallback for
 * the case where nobody told us, where "no advice" must not mean "retry at
 * once".
 *
 * The ceiling is deliberately `8s` while the server cap is `30s`: a client
 * ladder is a guess, a server delay is information, and the fallback must not
 * be the reason a reconnect arrives late.
 */
const MAX_REJOIN_ATTEMPTS = 5;
const REJOIN_BASE_DELAY_MS = 500;
const REJOIN_MAX_DELAY_MS = 8_000;

/**
 * Equal-jitter backoff for one attempt: `half + random(half)` of
 * `min(cap, base·2ⁿ)`.
 *
 * **Equal jitter, not full jitter.** Full jitter is uniform over
 * `[0, ceiling]`, so its expected delay is `ceiling/2` — but it can return
 * ~0, which is precisely the behaviour this function exists to remove: an
 * immediate re-dial against the dependency that just failed. Full jitter only
 * spreads the *herd*; equal jitter spreads the herd **and** guarantees a real
 * floor, so every attempt waits at least `ceiling/2`. The random half is what
 * stops N browser tabs that all lost the same stream from re-dialing in
 * lockstep, which a deterministic backoff would guarantee.
 */
function rejoinDelayMs(attempt: number): number {
  const ceiling = Math.min(REJOIN_MAX_DELAY_MS, REJOIN_BASE_DELAY_MS * 2 ** Math.max(0, attempt - 1));
  const half = Math.floor(ceiling / 2);
  return half + Math.floor(Math.random() * half);
}

function waitForReconnect(delay: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new ApiClientError("stopped"));
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      reject(new ApiClientError("stopped"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, delay);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function consumeChatStream(
  response: Response,
  options: {
    threadId: string;
    signal: AbortSignal;
    onUpdate: (messages: StreamMessage[], runId?: string) => void;
    /**
     * Fires for EVERY byte received, including the backend's `: heartbeat`
     * comment lines, which the decoder ignores on purpose. This is the only
     * hook that can tell "long tool call" from "connection gone" — an
     * `onUpdate` fires only on parseable frames, which stop during silence.
     */
    onActivity?: () => void;
    /**
     * Subagent tasks folded from `task_*` custom events. The array reference is
     * stable across frames with no subagent news, so passing it straight to
     * `setState` does not re-render.
     */
    onTasks?: (tasks: SubagentTask[]) => void;
    /**
     * The live execution plan folded from `todos_updated` custom events. Like
     * `onTasks`, the reference is stable across frames with no plan news, so it
     * can be handed straight to `setState`. `reportedAtAll: false` means the run
     * has not written a plan *yet* — which is different from an empty plan, and
     * the caller must not render a panel for it.
     */
    onTodos?: (plan: TodoPlan) => void;
    onEvent?: (event: ReplayGapEvent) => void;
    reconnect?: typeof apiFetch;
  },
): Promise<{ messages: StreamMessage[]; tasks: SubagentTask[]; todos: TodoPlan; runId?: string; sse: boolean }> {
  const contentType = response.headers?.get("Content-Type")?.split(";")[0].trim().toLowerCase();
  if (contentType && contentType !== "text/event-stream" && contentType !== "text/plain") {
    throw new ApiClientError("response");
  }
  const sse = contentType === "text/event-stream";
  let state = createSseState(runIdFromLocation(response.headers?.get("Content-Location"), options.threadId));
  let text = "";
  let attempts = 0;
  let retryDelay = 0;
  for (;;) {
    const reader = response.body?.getReader();
    if (!reader) return { messages: [], tasks: streamTasks(state), todos: streamTodos(state), runId: state.runId, sse };
    const decoder = new TextDecoder();
    const parser = createSseDecoder((frame) => {
      const previousGap = state.replayGap;
      state = reduceSse(state, frame);
      if (state.replayGap && state.replayGap !== previousGap) options.onEvent?.(state.replayGap);
      options.onUpdate(streamMessages(state), state.runId);
      options.onTasks?.(streamTasks(state));
      options.onTodos?.(streamTodos(state));
      if (state.failure) throw new StreamRunFailure(state.error ?? null);
    }, (delay) => { retryDelay = delay; });
    let transportFailed = false;
    try {
      for (;;) {
        let result: ReadableStreamReadResult<Uint8Array>;
        try {
          result = await reader.read();
        } catch {
          transportFailed = true;
          break;
        }
        if (options.signal.aborted) throw new ApiClientError("stopped");
        if (result.done) break;
        // Count every delivered byte before parsing, so a heartbeat comment
        // (which parses to nothing) still proves the stream is alive.
        options.onActivity?.();
        if (sse) {
          parser.push(result.value);
        } else {
          text += decoder.decode(result.value, { stream: true });
          options.onUpdate([{ id: "plain", runId: state.runId || "", content: text }], state.runId);
        }
      }
      if (!transportFailed) {
        if (sse) {
          parser.finish();
        } else {
          text += decoder.decode();
        }
      }
    } finally {
      try {
        await reader.cancel?.();
      } catch {}
      reader.releaseLock();
    }
    if (options.signal.aborted) throw new ApiClientError("stopped");
    if (!sse) {
      if (transportFailed) throw new ApiClientError("network");
      return { messages: text.trim() ? [{ id: "plain", runId: state.runId || "", content: text }] : [], tasks: streamTasks(state), todos: streamTodos(state), runId: state.runId, sse };
    }
    if (state.failure) throw new StreamRunFailure(state.error ?? null);
    if (state.ended) return { messages: streamMessages(state), tasks: streamTasks(state), todos: streamTodos(state), runId: state.runId, sse };
    if (!state.runId || !state.lastEventId) throw new StreamRunFailure(state.error ?? null);
    // 2 attempts used to be the whole budget: a laptop that slept for two
    // seconds was enough to lose the rest of an answer irreversibly, because
    // every byte after `lastEventId` was still on the server and the client had
    // already given up asking for it.
    if (attempts++ >= MAX_REJOIN_ATTEMPTS) throw new StreamRunFailure(state.error ?? null);
    // A `retry:` frame is the server's own instruction and outranks the ladder;
    // only its absence falls through to the client backoff.
    await waitForReconnect(retryDelay > 0 ? retryDelay : rejoinDelayMs(attempts), options.signal);
    if (options.signal.aborted) throw new ApiClientError("stopped");
    response = await (options.reconnect || apiFetch)(
      `/threads/${encodeURIComponent(options.threadId)}/runs/${encodeURIComponent(state.runId)}/join`,
      { signal: options.signal, headers: { Accept: "text/event-stream", "Last-Event-ID": state.lastEventId } },
    );
    if (response.headers?.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "text/event-stream") {
      throw new ApiClientError("response");
    }
  }
}
