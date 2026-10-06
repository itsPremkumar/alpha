/**
 * `@` tag grammar for the chat composer — the client's mirror of the server's
 * ONE mention grammar (`alpha.channels.mentions` in
 * `backend/packages/harness/alpha/channels/mentions.py`).
 *
 * Why a mirror is required, and why it has to be exact
 * -----------------------------------------------------
 * The backend already refused to guess. Its own docstring: "An unknown or
 * ambiguous handle resolves to NOTHING and says why. It never resolves to a near
 * match," because `@rev` silently landing on `reviewer` is exactly how a message
 * reaches a bot nobody named. A composer that inserts a token the server cannot
 * resolve re-creates that failure from the other end — in the one place the
 * operator is still able to see and fix it.
 *
 * So the single property this module exists to protect:
 *
 *   **Every token inserted here is a token the server can resolve.**
 *
 * Insertion always writes the canonical `@bot:<handle>` / `@role:<name>` /
 * `@everyone` spelling taken from the roster row, never the characters the
 * operator typed. Fuzzy ranking is therefore allowed to reorder the list while
 * being forbidden from changing what is written.
 *
 * What is mirrored and what is not
 * ---------------------------------
 * Mirrored: the token charset, case folding (and *only* case folding), the
 * `@bot:` / `@role:` / `@everyone` selector spellings, the `all`/`everyone`
 * aliases, exact-only handle resolution, and the fan-out ceiling.
 *
 * Not mirrored, and deliberately so:
 *
 * - **Roles are client-derived here.** `buildMentionRows` groups by the roster's
 *   `department` field because that is a real roster column. The server resolves
 *   `@role:` against *a room's own* role index — and `GroupChatService.post_message`
 *   passes no roles at all, so a role token addressed through a group room is
 *   currently unresolved there. The role rows say what they resolve to locally
 *   and never claim a dispatch.
 * - **A resolved handle is not a delivered run.** Resolution is pure string work.
 *   Only the `switch` mode changes routing, because only that changes the run's
 *   `assistant_id` / `bot_name`, which is what the server actually routes on.
 */

export type MentionKind = "bot" | "role" | "all";

/** Mirror of `MAX_TARGETS_PER_MESSAGE` in `alpha.channels.mentions`. */
export const MAX_TARGETS_PER_MESSAGE = 32;

/** Mirror of `ALL_SELECTOR_ALIASES`: `@all` is the fan-out selector, not a handle. */
export const ALL_SELECTOR_ALIASES: readonly string[] = ["all", "everyone"];

/** How many rows the palette renders before it discloses the remainder. */
export const MENTION_PALETTE_LIMIT = 10;

/** The complete token charset the server accepts: no spaces, no path separators. */
function isTokenChar(ch: string): boolean {
  return /[A-Za-z0-9_.\-]/.test(ch);
}

/**
 * Mirror of `normalise_handle`: case folding ONLY.
 *
 * Punctuation is not stripped — `rev-1` and `rev_1` are different handles, and
 * treating them as one is how a message lands on a bot nobody named.
 */
export function normaliseHandle(raw: string): string {
  return (raw || "").trim().toLowerCase();
}

/** The `@` token under the caret, or null when the caret is not inside one. */
export interface MentionTrigger {
  /** Offset of the `@` that opened the token. */
  start: number;
  /** Caret offset — the exclusive end of what the token currently occupies. */
  end: number;
  /** The namespace the operator has committed to with `@bot:` / `@role:`, else null. */
  kind: MentionKind | null;
  /** What has been typed after the `@` (and after any explicit `kind:`). */
  query: string;
}

/**
 * Detect the `@` token the caret currently sits in.
 *
 * Two rules keep a picker from hijacking text it does not own:
 *
 * 1. **A trigger must open a word** — start of input, or after whitespace / an
 *    opening delimiter. This is what keeps `user@example.com` and `src/lib/a@b`
 *    from opening an agent palette.
 * 2. **No whitespace may be inside the token.** A mention can never swallow a
 *    sentence, so the moment the operator types a space the token is closed and
 *    the picker goes away.
 *
 * No lookbehind is used anywhere in this module: it is unavailable in Safari
 * below 16.4 and this composer runs in a browser, not a build target.
 */
export function findMentionAtCaret(text: string, caret: number): MentionTrigger | null {
  const limit = Math.max(0, Math.min(caret, text.length));
  const head = text.slice(0, limit);
  const at = head.lastIndexOf("@");
  if (at < 0) return null;

  const before = at === 0 ? "" : head[at - 1];
  if (before !== "" && !/[\s([{<,;:'"]/.test(before)) return null;

  const typed = head.slice(at + 1);
  if (/\s/.test(typed)) return null;
  if (/[^A-Za-z0-9_.:\-]/.test(typed)) return null;

  // An explicit `kind:` prefix commits the namespace the operator chose.
  const qualified = /^(bot|role|everyone):([A-Za-z0-9_.\-]*)$/.exec(typed);
  if (qualified) {
    const kind: MentionKind = qualified[1] === "everyone" ? "all" : (qualified[1] as MentionKind);
    return { start: at, end: limit, kind, query: qualified[2] };
  }

  // A bare `all` / `everyone` is the fan-out selector, never a handle.
  if (ALL_SELECTOR_ALIASES.includes(typed)) {
    return { start: at, end: limit, kind: "all", query: typed };
  }

  return { start: at, end: limit, kind: null, query: typed };
}

/** One roster row, reduced to what a tag needs to be built and explained. */
export interface MentionAgent {
  /** The addressable handle — `BotProfile.name`, the roster's own key. */
  handle: string;
  displayName: string;
  role: string;
  department: string;
  /** Roster lifecycle word, kept verbatim; never snapped to a known enum. */
  status: string;
  avatar: string;
  model: string | null;
  capabilities: string[];
}

export type MentionRowMode = "mention" | "switch" | "role" | "everyone";

export interface MentionRow {
  /** Stable key and identity. Never a display name — those collide. */
  id: string;
  mode: MentionRowMode;
  /** Exactly what gets written, without the trailing space. Server-resolvable. */
  token: string;
  title: string;
  subtitle: string;
  /** Right-hand chip: the handle, the resolved count, or the department. */
  badge: string;
  avatar: string;
  /** Roster lifecycle word, verbatim. */
  status: string;
  /** Every handle/role this row resolves to — the disclosure behind `badge`. */
  resolves: string[];
  /** Set only on a `switch` row: the handle to make active. */
  switchHandle?: string;
  /**
   * Non-null when the row cannot be used, with the reason. A control that can
   * never succeed is rendered off with its reason rather than as pending.
   */
  refusal: string | null;
}

/** Every agent handle, folded, de-duplicated in first-seen order. */
export function rosterHandles(agents: MentionAgent[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const agent of agents) {
    const key = normaliseHandle(agent.handle);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  return out;
}

/**
 * Group the roster by `department` into a `@role:` index.
 *
 * `department` is used because it is a real roster column that group rules also
 * match against, so this is an index of the roster rather than an invented
 * taxonomy. It is still client-derived — see the module docstring.
 */
export function departmentRoleIndex(agents: MentionAgent[]): Record<string, string[]> {
  const index: Record<string, string[]> = {};
  for (const agent of agents) {
    const key = normaliseHandle(agent.department);
    if (!key) continue;
    (index[key] ||= []).push(normaliseHandle(agent.handle));
  }
  for (const key of Object.keys(index)) {
    index[key] = Array.from(new Set(index[key]));
  }
  return index;
}

/**
 * Build every tag the picker may offer.
 *
 * One row is one unambiguous insertion, so a row never has to guess what the
 * operator meant:
 *
 * - `mention`  — write `@bot:<handle>`; the text carries the tag.
 * - `switch`   — write `@bot:<handle>` AND make that agent the one this
 *                conversation runs on. This is the only mode that changes
 *                routing, because `assistant_id` / `bot_name` is what the server
 *                routes on.
 * - `role`     — write `@role:<department>`, resolving to every roster agent in
 *                that department.
 * - `everyone` — write `@everyone`, resolving to the whole roster.
 *
 * `allowSwitch` exists so a surface with no switch handler never offers a switch
 * row: a control that could only be a no-op is not a control.
 */
export function buildMentionRows(input: {
  agents: MentionAgent[];
  activeHandle?: string | null;
  allowSwitch?: boolean;
  /** Extra server-provided roles, merged over the derived department index. */
  roles?: Record<string, string[]>;
}): MentionRow[] {
  const { agents, activeHandle = null, allowSwitch = false, roles } = input;
  const rows: MentionRow[] = [];
  const handles = rosterHandles(agents);
  const active = normaliseHandle(activeHandle || "");
  const byHandle = new Map(agents.map((a) => [normaliseHandle(a.handle), a]));

  for (const agent of agents) {
    const handle = normaliseHandle(agent.handle);
    if (!handle) continue;
    rows.push({
      id: `bot:${handle}`,
      mode: "mention",
      token: `@bot:${handle}`,
      title: agent.displayName || handle,
      subtitle: agent.role || "Agent",
      badge: `@${handle}`,
      avatar: agent.avatar || "",
      status: agent.status || "",
      resolves: [handle],
      refusal: null,
    });
  }

  if (allowSwitch) {
    for (const agent of agents) {
      const handle = normaliseHandle(agent.handle);
      if (!handle || handle === active) continue;
      // Refuse rather than offer an unknown target: a switch to a handle the
      // roster cannot produce would fail at admission, not here.
      const known = byHandle.has(handle);
      rows.push({
        id: `switch:${handle}`,
        mode: "switch",
        token: `@bot:${handle}`,
        title: agent.displayName || handle,
        subtitle: "Make this the agent for this chat",
        badge: "bot mode",
        avatar: agent.avatar || "",
        status: agent.status || "",
        resolves: [handle],
        switchHandle: handle,
        refusal: known ? null : "The roster did not return this agent.",
      });
    }
  }

  const roleIndex = { ...departmentRoleIndex(agents) };
  for (const [role, members] of Object.entries(roles || {})) {
    const key = normaliseHandle(role);
    if (!key) continue;
    const existing = roleIndex[key] || [];
    roleIndex[key] = Array.from(new Set([...existing, ...members.map(normaliseHandle)]));
  }
  for (const role of Object.keys(roleIndex).sort()) {
    const members = roleIndex[role];
    if (members.length === 0) continue;
    rows.push({
      id: `role:${role}`,
      mode: "role",
      token: `@role:${role}`,
      title: `@role:${role}`,
      subtitle: members.length === 1 ? "1 agent · department tag" : `${members.length} agents · department tag`,
      badge: role,
      avatar: "",
      status: "",
      resolves: members,
      refusal:
        members.length > MAX_TARGETS_PER_MESSAGE
          ? `Would address ${members.length} agents, above the ${MAX_TARGETS_PER_MESSAGE} fan-out ceiling.`
          : null,
    });
  }

  rows.push({
    id: "everyone",
    mode: "everyone",
    token: "@everyone",
    title: "@everyone",
    subtitle:
      handles.length === 0
        ? "No agents in the roster"
        : handles.length === 1
          ? "1 agent"
          : `${handles.length} agents`,
    badge: "everyone",
    avatar: "",
    status: "",
    resolves: handles,
    refusal:
      handles.length === 0
        ? "The roster is empty, so this tag would address nobody."
        : handles.length > MAX_TARGETS_PER_MESSAGE
          ? `Would address ${handles.length} agents, above the ${MAX_TARGETS_PER_MESSAGE} fan-out ceiling.`
          : null,
  });

  return rows;
}

/**
 * Score one row against a typed query.
 *
 * Tiers, best first: exact handle, prefix, word-boundary, substring,
 * subsequence. This orders the list and nothing else — the inserted token is
 * always `row.token`, so a generous match cannot write the wrong handle.
 */
function scoreRow(row: MentionRow, query: string): number {
  if (!query) return 3;
  const needle = normaliseHandle(query);
  const candidates = [normaliseHandle(row.badge), normaliseHandle(row.title)];
  let best = -1;
  for (const candidate of candidates) {
    if (!candidate) continue;
    if (candidate === needle) best = Math.max(best, 4);
    else if (candidate.startsWith(needle)) best = Math.max(best, 3);
    else if (new RegExp(`(^|[^A-Za-z0-9_.\\-])${escapeRe(needle)}`).test(candidate)) best = Math.max(best, 2);
    else if (candidate.includes(needle)) best = Math.max(best, 1);
    else if (isSubsequence(needle, candidate)) best = Math.max(best, 0);
  }
  if (row.token.toLowerCase().includes(`@${needle}`)) best = Math.max(best, 3);
  return best;
}

function escapeRe(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\\-]/g, "\\$&");
}

function isSubsequence(needle: string, haystack: string): boolean {
  if (!needle) return false;
  let i = 0;
  for (const ch of haystack) {
    if (ch === needle[i]) i += 1;
    if (i === needle.length) return true;
  }
  return false;
}

export interface MentionFilter {
  /** Every row the query and namespace admit, best match first. */
  matched: MentionRow[];
  /** The bounded slice the palette renders. */
  visible: MentionRow[];
  /** How many matched rows the bound hid — disclosed, never silently dropped. */
  hidden: number;
}

/**
 * Narrow the built rows to one `@` token.
 *
 * An explicit namespace (`@bot:`, `@role:`, `@everyone`) is a commitment, not a
 * filter hint: it restricts the list to that namespace rather than showing rows
 * the operator already ruled out.
 */
export function filterMentionRows(rows: MentionRow[], trigger: MentionTrigger, limit = MENTION_PALETTE_LIMIT): MentionFilter {
  const namespace =
    trigger.kind === "bot"
      ? new Set(["mention", "switch"])
      : trigger.kind === "role"
        ? new Set(["role"])
        : trigger.kind === "all"
          ? new Set(["everyone"])
          : null;

  const scored: Array<{ row: MentionRow; score: number; index: number }> = [];
  rows.forEach((row, index) => {
    if (namespace && !namespace.has(row.mode)) return;
    const score = scoreRow(row, trigger.query);
    if (score < 0) return;
    scored.push({ row, score, index });
  });

  scored.sort((a, b) => (b.score - a.score) || (a.index - b.index));
  const matched = scored.map((s) => s.row);
  return {
    matched,
    visible: matched.slice(0, Math.max(0, limit)),
    hidden: Math.max(0, matched.length - Math.max(0, limit)),
  };
}

/**
 * Replace exactly the `@` token under the caret with a server-resolvable tag.
 *
 * Only the span is touched, so text before and after the token — including a
 * trailing sentence — survives verbatim. A trailing space is appended so the
 * operator can keep typing, which is the detail that makes the interaction feel
 * continuous rather than fighting the caret.
 */
export function applyMention(text: string, trigger: MentionTrigger, row: MentionRow): { text: string; caret: number } {
  const inserted = `${row.token} `;
  const next = `${text.slice(0, trigger.start)}${inserted}${text.slice(trigger.end)}`;
  return { text: next, caret: trigger.start + inserted.length };
}

export interface MentionResolution {
  /** Every concrete addressee, de-duplicated, in first-mention order. */
  resolvedHandles: string[];
  /** One entry per token that addressed nobody, with the reason. */
  unresolved: Array<{ raw: string; reason: string }>;
  /** True when every token in the text resolved. */
  ok: boolean;
}

/**
 * The server's resolution, run client-side to preview it before send.
 *
 * This mirrors `parse_mentions` so the composer can say which tags will address
 * nobody *before* the run. The server remains the authority — a preview that
 * disagrees is a preview bug, and the reasons below are deliberately worded to
 * match it so the two do not drift in the operator's eyes.
 */
export function parseMentions(
  text: string,
  roster: string[],
  roles?: Record<string, string[]>,
): MentionResolution {
  const body = text || "";
  const known = new Map<string, string[]>();
  for (const handle of roster) {
    const key = normaliseHandle(handle);
    if (!key) continue;
    const bucket = known.get(key) || [];
    bucket.push(handle);
    known.set(key, bucket);
  }
  const roleIndex: Record<string, string[]> = {};
  for (const [role, members] of Object.entries(roles || {})) {
    const key = normaliseHandle(role);
    if (!key) continue;
    roleIndex[key] = Array.from(new Set(members.map(normaliseHandle).filter(Boolean)));
  }

  const pattern = /@(bot|role|everyone):([A-Za-z0-9_.\-]+)|@([A-Za-z0-9_.\-]+)/g;
  const resolved: string[] = [];
  const seen = new Set<string>();
  const unresolved: Array<{ raw: string; reason: string }> = [];
  const emitted = new Set<string>();

  let match: RegExpExecArray | null;
  while ((match = pattern.exec(body)) !== null) {
    const raw = match[0];
    const explicit = match[1];
    const token = normaliseHandle(explicit ? match[2] ?? "" : match[3] ?? "");
    let kind: MentionKind;
    if (explicit) {
      kind = explicit === "everyone" ? "all" : (explicit as MentionKind);
    } else {
      kind = ALL_SELECTOR_ALIASES.includes(token) ? "all" : "bot";
    }

    if (emitted.has(`${kind}:${token}`)) continue;
    emitted.add(`${kind}:${token}`);

    if (kind === "all") {
      const everyone = Array.from(known.keys()).sort();
      if (everyone.length === 0) {
        unresolved.push({ raw, reason: "@everyone resolved to nobody (the roster is empty)" });
        continue;
      }
      if (everyone.length > MAX_TARGETS_PER_MESSAGE) {
        unresolved.push({
          raw,
          reason: `@everyone would address ${everyone.length} handles, above the ${MAX_TARGETS_PER_MESSAGE} fan-out ceiling`,
        });
        continue;
      }
      for (const handle of everyone) if (!seen.has(handle)) { seen.add(handle); resolved.push(handle); }
      continue;
    }

    if (kind === "role") {
      const members = roleIndex[token];
      if (!members || members.length === 0) {
        const defined = Object.keys(roleIndex).sort();
        unresolved.push({
          raw,
          reason: `unknown role selector; defined roles: ${defined.length ? defined.join(", ") : "(none defined)"}`,
        });
        continue;
      }
      const absent = members.filter((m) => !known.has(m));
      if (absent.length > 0) {
        unresolved.push({
          raw,
          reason: `role '${token}' references ${absent.length} handle(s) not in the roster: ${absent.sort().join(",")}`,
        });
        continue;
      }
      if (members.length > MAX_TARGETS_PER_MESSAGE) {
        unresolved.push({
          raw,
          reason: `role '${token}' would address ${members.length} handles, above the ${MAX_TARGETS_PER_MESSAGE} fan-out ceiling`,
        });
        continue;
      }
      for (const handle of members) if (!seen.has(handle)) { seen.add(handle); resolved.push(handle); }
      continue;
    }

    const matches = known.get(token);
    if (!matches || matches.length === 0) {
      unresolved.push({
        raw,
        reason: "unknown handle; this tag addresses nobody. Use @role:<name> for a role selector.",
      });
      continue;
    }
    if (matches.length > 1) {
      unresolved.push({
        raw,
        reason: `ambiguous handle; ${matches.length} roster entries match: ${[...matches].sort().join(",")}`,
      });
      continue;
    }
    if (!seen.has(matches[0])) {
      seen.add(matches[0]);
      resolved.push(matches[0]);
    }
  }

  return { resolvedHandles: resolved, unresolved, ok: unresolved.length === 0 };
}

/**
 * Every `@token` currently in the draft, whether or not it resolves.
 *
 * Used to decide whether the tag status strip is warranted at all: an empty
 * draft and a draft with one dead tag are different states and must not render
 * the same way.
 */
export function countMentionTokens(text: string): number {
  const pattern = /@(bot|role|everyone):([A-Za-z0-9_.\-]+)|@([A-Za-z0-9_.\-]+)/g;
  let count = 0;
  while (pattern.exec(text || "") !== null) count += 1;
  return count;
}