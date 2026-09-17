import { apiFetch, ApiClientError } from "./api-client";
import { createSseDecoder, createSseState, reduceSse, runIdFromLocation, streamMessages, StreamMessage, ReplayGapEvent } from "./sse-reducer";

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
    onEvent?: (event: ReplayGapEvent) => void;
    reconnect?: typeof apiFetch;
  },
): Promise<{ messages: StreamMessage[]; runId?: string; sse: boolean }> {
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
    if (!reader) return { messages: [], runId: state.runId, sse };
    const decoder = new TextDecoder();
    const parser = createSseDecoder((frame) => {
      const previousGap = state.replayGap;
      state = reduceSse(state, frame);
      if (state.replayGap && state.replayGap !== previousGap) options.onEvent?.(state.replayGap);
      options.onUpdate(streamMessages(state), state.runId);
      if (state.failure) throw new ApiClientError("response");
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
      return { messages: text.trim() ? [{ id: "plain", runId: state.runId || "", content: text }] : [], runId: state.runId, sse };
    }
    if (state.failure) throw new ApiClientError("response");
    if (state.ended) return { messages: streamMessages(state), runId: state.runId, sse };
    if (!state.runId || !state.lastEventId || attempts++ >= 2) throw new ApiClientError("response");
    await waitForReconnect(retryDelay, options.signal);
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
