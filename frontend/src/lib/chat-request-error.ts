import { chatSupportLine, type ChatSupportId } from "./chat-support-id";

export type ChatRequestFailure = {
  kind: "http" | "network" | "stream" | "empty" | "stopped";
  status?: number;
  partialArchived?: boolean;
  /**
   * The run's support identity (`code` + correlation id), when the Gateway sent
   * one. Rendered as a separate disclosure, never merged into the prose.
   */
  supportId?: ChatSupportId | null;
};

/**
 * Append the run's support identity to a failure sentence, when there is one.
 *
 * Kept out of the sentences themselves on purpose: those are fixed, tested copy
 * about what the *client* knows, and a support id is server-reported evidence
 * about a specific run. One returns a reason, the other identifies an incident,
 * and folding them together would let a sanitized sentence become a place where
 * untrusted server text lands.
 */
function withSupport(base: string, failure: ChatRequestFailure): string {
  const line = chatSupportLine(failure.supportId);
  return line ? `${base} ${line}.` : base;
}

export function chatRequestErrorMessage(failure: ChatRequestFailure): string {
  switch (failure.kind) {
    case "http": {
      const status = failure.status;
      const label = typeof status === "number" && Number.isInteger(status) && status >= 100 && status <= 599
        ? ` (HTTP ${status})`
        : "";
      return withSupport(
        `Request failed${label}. No assistant response was received. Review your draft and try again.`,
        failure,
      );
    }
    case "network":
      return withSupport(
        "The request could not be completed. No assistant response was received. Check your connection before retrying; the server may still be processing the request and its durable run can continue after reconnect.",
        failure,
      );
    case "stream":
      return withSupport(
        failure.partialArchived === false
          ? "The response stream was interrupted. The partial response below is incomplete and could not be added to the local history archive. The server may still be running; reconnect or reload Runs before retrying."
          : "The response stream was interrupted. Any partial response below is incomplete; it is kept in the local history archive but is not treated as a completed answer. The server may still be running; reconnect or reload Runs before retrying.",
        failure,
      );
    case "empty":
      return withSupport(
        "The server returned no response content. No assistant answer was saved. Check Runs before retrying.",
        failure,
      );
    case "stopped":
      return withSupport(
        failure.partialArchived === false
          ? "The response stream was stopped locally. The partial response below is incomplete and could not be added to the local history archive. Server cancellation is not confirmed here; check Runs before retrying."
          : "The response stream was stopped locally. Any partial response below is incomplete; it is kept in the local history archive but is not treated as a completed answer. Server cancellation is not confirmed here; check Runs before retrying.",
        failure,
      );
  }
}
