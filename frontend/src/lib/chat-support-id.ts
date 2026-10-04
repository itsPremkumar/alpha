/**
 * The support identity of a failed run.
 *
 * ## Why this module exists
 *
 * The Gateway has always sent a coded, correlated failure reason on
 * `event: error` (`code` + `correlation_id`), `sse-reducer.ts` has always
 * parsed it into `SseState.error`, and `tool-status-honesty.test.mjs` has
 * always pinned that the parse is correct. Nothing ever read it.
 *
 * `consumeChatStream` threw a bare `ApiClientError("response")`, `ChatView`
 * collapsed that to `{ kind: "stream" }`, and `chatRequestErrorMessage`
 * returned fixed prose — so every failed run reached the operator as
 * "The response stream was interrupted", which is true of every failed run
 * ever seen. The one piece of evidence that made a run diagnosable was
 * parsed, tested, and discarded. That is a silent failure with a passing
 * test suite: the defect was invisible precisely *because* the parse worked.
 *
 * ## What may be shown, and what may not
 *
 * The honest fix is NOT to render the server's `message` into the UI. A run
 * failure's message is derived from tool output, provider text and file
 * contents, so it is untrusted content by construction — and
 * `chat-request-error.test.mjs` deliberately pins that a response body
 * containing a secret or a `<script>` tag must never reach the transcript.
 * Relaxing that to "show the server's message" would trade a missing reason
 * for an injection surface.
 *
 * What is safe — and is what an operator actually needs — is the run's
 * **identity**: a stable code and a correlation id. Those are server-generated
 * tokens, not user content, and they are exactly what a second operator needs
 * to find the matching trace in the Gateway's own logs. So:
 *
 * - `code` and `correlationId` are shown, after a strict shape check.
 * - `message` is NOT shown here. It stays on `SseState.error` for the surfaces
 *   that render server-owned detail deliberately (the run inspector), and is
 *   deliberately dropped from the chat failure path.
 *
 * This is the same honesty rule the rest of the UI is built on: the count must
 * be a count, and an identifier must be an identifier. A support id that has
 * been trimmed, or that silently absorbed a giant untrusted string, would be a
 * fabricated one.
 */

/** Longest support id rendered. A correlation id is a token; nobody pastes 4 KB. */
const MAX_SUPPORT_ID_CHARS = 96;

/**
 * Characters permitted in a rendered identifier.
 *
 * Deliberately narrower than "printable": no angle brackets, no ampersand, no
 * quotes or backticks, and no whitespace. A support id is displayed as text, so
 * the cheap guarantee is that it cannot introduce markup or a line break, and
 * that check does not depend on whatever escaping the render site happens to do
 * today. Anything outside this set is not an identifier we are willing to show.
 */
const SUPPORT_ID_PATTERN = /^[A-Za-z0-9._:@/+-]+$/;

/** Prefixes a rendered line so the tokens are never mistaken for prose. */
const CODE_LABEL = "Error code";
const CORRELATION_LABEL = "Support id";

export type ChatSupportId = {
  /** The stable server error code, e.g. `run_failed`. */
  code?: string;
  /** The correlation / trace id an operator can search the Gateway logs for. */
  correlationId?: string;
};

/**
 * Accept a server-reported identifier only if it is plausibly an identifier.
 *
 * Returns `undefined` rather than a cleaned-up string: trimming a value into
 * shape would render something the server did not report, which is the exact
 * fabrication this module exists to avoid. A hostile or malformed value is
 * therefore *absent*, and an absent id renders no line at all — a quiet failure
 * here is the correct one, because the surrounding prose already states that
 * the run failed.
 */
function safeIdentifier(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > MAX_SUPPORT_ID_CHARS) return undefined;
  return SUPPORT_ID_PATTERN.test(trimmed) ? trimmed : undefined;
}

/**
 * Reduce a server error payload to the two tokens safe to render.
 *
 * Exported separately from the sentence builder so the *filtering* is testable
 * on its own: the security property (an untrusted message never becomes a
 * support id) is the part worth pinning, not the wording around it.
 */
export function chatSupportId(detail: ChatSupportId | null | undefined): ChatSupportId | null {
  if (!detail) return null;
  const code = safeIdentifier(detail.code);
  const correlationId = safeIdentifier(detail.correlationId);
  // Both refused: there is no support identity to show, so render nothing.
  // Returning `null` (not `{}`) keeps `supportLine` from emitting an empty label.
  if (!code && !correlationId) return null;
  return { ...(code ? { code } : {}), ...(correlationId ? { correlationId } : {}) };
}

/**
 * The one line appended to a chat failure message, or `null` when there is no
 * support identity to add.
 *
 * Returning `null` rather than an empty string matters: the caller appends the
 * result directly, and a stray separator with nothing after it is a visible
 * artefact on a surface whose entire job is to say exactly what is known.
 */
export function chatSupportLine(detail: ChatSupportId | null | undefined): string | null {
  const id = chatSupportId(detail);
  if (!id) return null;
  const parts: string[] = [];
  if (id.code) parts.push(`${CODE_LABEL}: ${id.code}`);
  if (id.correlationId) parts.push(`${CORRELATION_LABEL}: ${id.correlationId}`);
  return parts.length ? parts.join(" · ") : null;
}
