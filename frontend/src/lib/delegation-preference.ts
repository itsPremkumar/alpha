/**
 * Persistence for the composer's delegation mode.
 *
 * **The gap this closes.** `ChatView` held the mode in plain
 * `useState("off")`, so an operator who selected `auto` lost it on the next
 * page load and the flagship capability silently went back to answering without
 * delegating. Nothing else in the composer had that defect - model and
 * reasoning effort are both restored from `localStorage` - which made
 * delegation the odd one out rather than a deliberate choice.
 *
 * **Why it is not a one-line read.** A stored value is only safe to restore if
 * the current catalog still offers it. `type:` rows name a subagent from
 * `config.yaml`, and a deployment that removes or renames one would otherwise
 * come back with a selector sitting on a mode that resolves to nothing - the
 * same mismatch the reasoning-effort restore refuses to produce. So the stored
 * value is reconciled against the catalog and **discarded** when it does not
 * survive, falling back to `off` rather than rendering a dead choice.
 *
 * Reads and writes are total: a `localStorage` that throws (private mode,
 * disabled storage, quota) must not take the composer down with it, so every
 * entry point degrades to "no stored preference".
 */

export const DELEGATION_MODE_STORAGE_KEY = "alpha_delegation_mode";

/** The two modes that are not catalog entries, and so always restorable. */
const ALWAYS_VALID = new Set(["off", "auto"]);

/** The prefixes that name something the catalog must actually carry. */
const PREFIXES = ["type:", "category:"] as const;

export type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** The mode used when nothing valid is stored. Matches the server's own posture. */
export const DEFAULT_DELEGATION_MODE = "off";

/**
 * The browser's `localStorage`, or `null` where there is none.
 *
 * Reads the global **inside the function** rather than at module scope: this
 * module is imported by a server-rendered tree, and touching `window` at import
 * time would throw before React ever renders. Returns `null` rather than a
 * throwaway object so callers get one code path for "no storage".
 */
export function defaultStorage(): StorageLike | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage ?? null;
  } catch {
    // Some browsers throw on the *access* to localStorage, not just its use.
    return null;
  }
}

/**
 * Is this a structurally valid delegation mode?
 *
 * Structural only - it cannot know whether `type:deep-security` exists. That is
 * `reconcileDelegationMode`'s job, and keeping the two apart is what lets a
 * persisted blob be sanity-checked before it is compared against a catalog.
 */
export function isDelegationModeShape(value: unknown): value is string {
  if (typeof value !== "string" || !value) return false;
  if (ALWAYS_VALID.has(value)) return true;
  return PREFIXES.some((prefix) => value.startsWith(prefix) && value.length > prefix.length);
}

/**
 * Reconcile a stored mode against the modes the catalog currently offers.
 *
 * Returns the mode to use, and whether the stored value survived. A caller that
 * wants to prune its storage needs the flag; one that only wants a mode can
 * ignore it.
 *
 * `available` is the set the select actually renders. An empty set means the
 * catalog has not loaded yet, and is treated as **"cannot verify"** rather than
 * "nothing is valid" - dropping the preference because a read has not landed
 * yet would make the setting depend on load ordering.
 */
export function reconcileDelegationMode(
  stored: unknown,
  available: Iterable<string>,
): { mode: string; restored: boolean } {
  if (!isDelegationModeShape(stored)) return { mode: DEFAULT_DELEGATION_MODE, restored: false };
  const set = new Set(available);
  if (set.size === 0) return { mode: stored, restored: true };
  // `off`/`auto` are mode kinds rather than catalog rows, so they only need to
  // survive if the catalog has actually rendered them.
  if (ALWAYS_VALID.has(stored)) {
    return set.has(stored) ? { mode: stored, restored: true } : { mode: DEFAULT_DELEGATION_MODE, restored: false };
  }
  return set.has(stored) ? { mode: stored, restored: true } : { mode: DEFAULT_DELEGATION_MODE, restored: false };
}

/** Read the stored mode. Never throws; unreadable storage reads as "none". */
export function readStoredDelegationMode(storage: StorageLike | null | undefined): string | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(DELEGATION_MODE_STORAGE_KEY);
    return isDelegationModeShape(raw) ? raw : null;
  } catch {
    return null;
  }
}

/**
 * Persist the mode, and clear the key when the mode is the default.
 *
 * Storing the default would be harmless but pointless, and clearing keeps the
 * "nothing was chosen" state distinguishable from "off was chosen" for anything
 * that ever wants to know whether an operator has an opinion yet.
 */
export function storeDelegationMode(storage: StorageLike | null | undefined, mode: string): boolean {
  if (!storage || !isDelegationModeShape(mode)) return false;
  try {
    if (mode === DEFAULT_DELEGATION_MODE) storage.removeItem(DELEGATION_MODE_STORAGE_KEY);
    else storage.setItem(DELEGATION_MODE_STORAGE_KEY, mode);
    return true;
  } catch {
    return false;
  }
}