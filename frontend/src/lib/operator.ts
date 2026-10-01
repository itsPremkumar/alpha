/**
 * Who is operating this workspace, resolved honestly.
 *
 * ## Why this module exists
 *
 * Five call sites used to hardcode a developer's initials as the operator
 * identity (`userName = "MK"` in `ChatShellLanding`, `WorkspaceTopBar`, and
 * three `ChatView` props). Every visitor to a fresh install was therefore
 * greeted as a specific person who had never used it, and the profile menu
 * claimed that person was "Lead Administrator". That is a fabricated fact in
 * the one surface that is supposed to establish trust, so it is resolved here
 * instead of being written into a default parameter.
 *
 * ## The honesty rule this module keeps
 *
 * **The gateway authenticates requests but exposes no display name.** Auth here
 * is a session cookie plus PAT scopes (`app/gateway/auth/`), and there is no
 * `GET /api/auth/me` returning a person's name — so the client has nothing to
 * read and must not manufacture something. This module therefore does *not*
 * invent a name from the OS, the hostname, a decoded JWT, or a guess. It reports
 * exactly two things:
 *
 * - `name: null` — nothing was configured, so the UI must not print a name.
 * - `initials` — a neutral operator glyph, not a person's initials.
 *
 * When an operator *does* want a name, they type it once in Settings; it is
 * stored locally under `alpha_operator_name` and read back here. That is the
 * only path that produces a name, and it is the operator's own claim about
 * themselves rather than something the app decided. If a real identity route
 * is ever added, this is the one module to read it in — the five call sites
 * should not each grow their own fetch.
 *
 * Mirrors the pattern `frontend/AGENTS.md` states for the client layer: absent
 * optional values map to `null` and are rendered as unknown, never coerced into
 * a plausible-looking zero or empty string.
 */

const NAME_STORAGE_KEY = "alpha_operator_name";
const NAME_MAX_LENGTH = 64;

/** The generic glyph shown when no operator name has been configured. */
export const ANONYMOUS_INITIALS = "?";
/** Shown wherever a name is required but absent. Never a person's name. */
export const ANONYMOUS_LABEL = "Operator";
/** Shown wherever a role is required but unknown. */
export const ANONYMOUS_ROLE = "Local operator";

export interface OperatorIdentity {
  /** The operator's own name, or `null` when none is configured. */
  name: string | null;
  /** A display glyph. `ANONYMOUS_INITIALS` when `name` is `null`. */
  initials: string;
  /** The role line. `ANONYMOUS_ROLE` when the name is absent. */
  role: string;
  /** True only when the operator explicitly configured a name. */
  configured: boolean;
}

/**
 * Derive initials from a name the operator typed.
 *
 * Up to two leading characters, uppercased. Exported for the test that pins
 * this rule, because the previous `"MK"` was a literal that no test could
 * check — nothing asserted that a name had to come from somewhere.
 *
 * A name that trims to nothing is `null`, never `""` rendered as a blank
 * avatar, which would read as a rendering failure rather than an absent value.
 */
export function operatorInitials(name: string | null): string {
  if (typeof name !== "string") return ANONYMOUS_INITIALS;
  const trimmed = name.trim();
  if (!trimmed) return ANONYMOUS_INITIALS;
  const words = trimmed.split(/\s+/).filter(Boolean);
  if (words.length === 0) return ANONYMOUS_INITIALS;
  const first = words[0]!;
  if (words.length === 1) return first.slice(0, 2).toUpperCase();
  return `${first[0]!}${words[words.length - 1]![0]!}`.toUpperCase();
}

/**
 * Normalise a name typed into Settings.
 *
 * Collapses whitespace, enforces a length cap, and maps the empty result to
 * `null` so clearing the field is the supported way to *unset* the name
 * rather than leaving a blank that renders as an empty avatar.
 */
export function normalizeOperatorName(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const collapsed = raw.replace(/\s+/g, " ").trim();
  if (!collapsed) return null;
  return collapsed.slice(0, NAME_MAX_LENGTH);
}

/** Build an identity from an already-resolved name. Pure; no browser access. */
export function operatorIdentity(name: string | null): OperatorIdentity {
  const normalized = normalizeOperatorName(name);
  if (normalized === null) {
    return {
      name: null,
      initials: ANONYMOUS_INITIALS,
      role: ANONYMOUS_ROLE,
      configured: false,
    };
  }
  return {
    name: normalized,
    initials: operatorInitials(normalized),
    role: ANONYMOUS_ROLE,
    configured: true,
  };
}

/**
 * The greeting the chat landing page shows.
 *
 * The previous copy was unconditionally "Welcome back, {userName}!", which is
 * a lie on a first run: there is no "back" to welcome anyone from, and the
 * name was a developer's. The honest split is "Welcome back" only once
 * something proves a previous session, and a first-run greeting otherwise.
 *
 * `returning` is supplied by the caller from a real signal (an existing
 * conversation), never defaulted to `true` to make the copy nicer.
 */
export function landingGreeting(identity: OperatorIdentity, returning: boolean): string {
  if (returning && identity.name) return `Welcome back, ${identity.name}!`;
  if (returning) return "Welcome back!";
  if (identity.name) return `Welcome, ${identity.name}!`;
  return `Welcome to ${"Alpha"}!`;
}

/** Read the configured name. Returns `null` when absent or unreadable. */
export function readOperatorName(): string | null {
  try {
    if (typeof window === "undefined") return null;
    return normalizeOperatorName(window.localStorage.getItem(NAME_STORAGE_KEY));
  } catch {
    // A blocked or full localStorage is a real state, not a licence to invent
    // a name. The UI degrades to the anonymous identity.
    return null;
  }
}

/** Persist (or clear, with `null`) the operator's own name. */
export function writeOperatorName(name: string | null): boolean {
  try {
    if (typeof window === "undefined") return false;
    const normalized = normalizeOperatorName(name);
    if (normalized === null) window.localStorage.removeItem(NAME_STORAGE_KEY);
    else window.localStorage.setItem(NAME_STORAGE_KEY, normalized);
    // Tell this tab too, not just the others.
    window.dispatchEvent(new Event(OPERATOR_NAME_CHANGED_EVENT));
    return true;
  } catch {
    return false;
  }
}

/** Read the full identity from storage. Safe to call during render. */
export function currentOperatorIdentity(): OperatorIdentity {
  return operatorIdentity(readOperatorName());
}

/**
 * Fired after `writeOperatorName` so open components can re-read the identity
 * without a reload. `storage` only fires in *other* tabs, so it cannot carry
 * this change to the tab that made it - hence an explicit event as well.
 */
export const OPERATOR_NAME_CHANGED_EVENT = "alpha:operator-name-changed";

/**
 * Subscribe to operator-name changes from this tab and from other tabs.
 * Returns an unsubscribe function. Degrades to a no-op without a window.
 */
export function subscribeOperatorName(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = () => onChange();
  window.addEventListener(OPERATOR_NAME_CHANGED_EVENT, handler);
  window.addEventListener("storage", handler);
  return () => {
    window.removeEventListener(OPERATOR_NAME_CHANGED_EVENT, handler);
    window.removeEventListener("storage", handler);
  };
}

export const OPERATOR_NAME_STORAGE_KEY = NAME_STORAGE_KEY;
