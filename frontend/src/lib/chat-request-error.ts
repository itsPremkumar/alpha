import { chatSupportLine, type ChatSupportId } from "./chat-support-id";

export type ChatRequestFailure = {
  kind: "http" | "network" | "stream" | "empty" | "stopped";
  status?: number;
  detail?: string | null;
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

/** Keep useful Gateway diagnostics while removing markup, control chars, and credential-shaped values. */
function safeGatewayDetail(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const detail = value
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, " ")
    .replace(/<[^>]*>/g, " ")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [redacted]")
    .replace(/\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|authorization)(\s*[:=]\s*)[^\s,;]+/gi, "$1$2[redacted]")
    .replace(/\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)=([^&\s]+)/gi, "$1=[redacted]")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 300);
  return detail || null;
}

export function chatRequestErrorMessage(failure: ChatRequestFailure): string {
  switch (failure.kind) {
    case "http": {
      const status = failure.status;
      const label = typeof status === "number" && Number.isInteger(status) && status >= 100 && status <= 599
        ? ` (HTTP ${status})`
        : "";
      const serverFailure = typeof status === "number" && status >= 500 && status <= 599;
      const detail = safeGatewayDetail(failure.detail);
      return withSupport(
        serverFailure
          ? `The Gateway failed${label}. No assistant response was received.${detail ? ` Reason: ${detail}.` : ""} Your draft is preserved. Check Runs to see whether the request was recorded before retrying.`
          : `The Gateway rejected the request${label}.${detail ? ` Reason: ${detail}.` : ""} No assistant response was received. Your draft is preserved; check the request settings before retrying.`,
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
