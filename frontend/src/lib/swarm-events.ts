/**
 * swarm-events.ts — consuming one swarm's ordered event stream.
 *
 * `GET /api/swarms/{swarm_id}/events/stream` is server-sent events, and two
 * properties of that stream decide the whole design here:
 *
 * 1. Every frame is NAMED by the runtime's event type (`SWARM_STARTED`,
 *    `TASK_FAILED`, …) — a vocabulary owned by the backend's event journal.
 *    The browser `EventSource` delivers named events only to listeners
 *    registered for that exact name, so a client that must react to *any*
 *    change would have to freeze the vocabulary into a listener list that
 *    silently stops seeing event types the backend adds later. Reading the
 *    response body directly treats every frame as the one thing this client
 *    needs — a change notification — with no vocabulary coupling.
 * 2. `EventSource` cannot send the Gateway's session/CSRF headers, while
 *    `apiFetch` can, so the subscription travels the same auth path as every
 *    other read.
 *
 * The stream is a notification, never a second source of truth: `onEvent`
 * means "something changed — re-read the detail you trust". `onEnd` is the
 * server's terminal frame (the swarm reached a final verdict, or the server's
 * observation window closed). `onError` carries the real reason so the caller
 * can disclose why it stopped streaming instead of going quiet.
 */

import { apiFetch } from "./api-client";
import { errMsg } from "./http";

export interface SwarmEventStreamHandlers {
  /** One frame arrived. Callers re-read authoritative state; never parse this as state. */
  onEvent: () => void;
  /** The server declared the stream finished (`event: end`). No `onError` follows. */
  onEnd: () => void;
  /** The stream failed. `reason` is the real one, for a visible fallback note. */
  onError: (reason: string) => void;
}

export interface SwarmEventStreamHandle {
  /** Stop consuming. Idempotent; no `onEnd`/`onError` fires after this. */
  close: () => void;
}

/** One SSE frame is an `event: end` terminal declaration. */
function isEndFrame(frame: string): boolean {
  return frame.split("\n").some((line) => line.startsWith("event:") && line.slice(6).trim() === "end");
}

export function subscribeSwarmEvents(swarmId: string, handlers: SwarmEventStreamHandlers): SwarmEventStreamHandle {
  const controller = new AbortController();
  let closed = false;

  const close = () => {
    closed = true;
    controller.abort();
  };
  const fail = (reason: string) => {
    if (closed) return;
    close();
    handlers.onError(reason);
  };

  void (async () => {
    let response: Response;
    try {
      response = await apiFetch(`/swarms/${encodeURIComponent(swarmId)}/events/stream`, { signal: controller.signal });
    } catch (err) {
      fail(errMsg(err));
      return;
    }
    const body = response.body;
    if (!body) {
      fail("the Gateway returned no event-stream body");
      return;
    }
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (closed) return;
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (isEndFrame(frame)) {
            close();
            handlers.onEnd();
            return;
          }
          handlers.onEvent();
          boundary = buffer.indexOf("\n\n");
        }
      }
    } catch (err) {
      fail(errMsg(err));
      return;
    }
    // Clean EOF without an `end` frame: the server closed the observation
    // window (its own timeout). Treat it like `end` — the caller re-reads and
    // decides whether anything is still refreshable.
    if (!closed) {
      close();
      handlers.onEnd();
    }
  })();

  return { close };
}
