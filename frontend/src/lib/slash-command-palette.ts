/**
 * The `/` palette's derivation, kept out of `Composer.tsx` so it is testable
 * without React and so the composer cannot grow a second, quieter filter.
 *
 * **Why this module exists.** The palette used to derive its rows inline with
 * two rules that made "every command" unreachable:
 *
 * 1. `.slice(0, 8)` — the real registry is `commands/catalog.py`, ~125 rows, so
 *    typing `/` showed eight of them and disclosed nothing. A bounded list with
 *    no count reads as the whole catalog.
 * 2. `input.includes(" ")` — the palette closed on the first space. The catalog
 *    is overwhelmingly *multi-word* (`/agent ask`, `/verify deep`, `/security
 *    lockdown`), so after one space not a single subcommand could be offered.
 *
 * Both are removed here. Nothing is capped any more: `rows` carries every
 * command the registry holds for the token, and the caller renders them in a
 * bounded, scrolling panel. The bound moved to the *renderer*, where it can say
 * what it hid, instead of living in the filter where it could not.
 */

/** One row the `/` palette can offer, in the shape the composer needs. */
export interface PaletteCommand {
  command: string;
  category: string;
  description: string;
  usage: string;
  /**
   * Whether the backend reported a bound handler.
   *
   * Tri-state on purpose: `false` is the server saying "this row answers
   * `unimplemented`", while `null` is "the read did not report it". Snapping
   * both to a dash would state as a measurement what is only an absence, and
   * `execute_slash_command_tool` genuinely refuses handler-less rows
   * (`docs/COMMAND_HONESTY.md`, `slash_command_without_handler` in
   * `docs/SELF_AWARENESS.md`), so a row without one is not a capability the
   * palette may present as if it were runnable.
   */
  hasHandler: boolean | null;
}

export interface SlashCommandPalette {
  /** Rows in pick order. Empty when nothing matched. */
  rows: PaletteCommand[];
  /** Whether this draft is a `/` token the palette can complete at all. */
  applicable: boolean;
  /** Whitespace-normalised prefix the rows were matched against. */
  prefix: string;
  /** How many commands matched. Equals `rows.length` — nothing is truncated. */
  matched: number;
  /** Registry size behind this result, so "8 of 125" is expressible. */
  total: number;
}

/**
 * Collapse the draft to a comparable command token.
 *
 * The composer always writes `${command} ` on selection, so the draft it hands
 * back carries a trailing space; collapsing runs and trimming is what lets
 * `/agent ` mean "list `/agent`'s subcommands" instead of "no match".
 */
export function normaliseCommandDraft(draft: string): string {
  return draft.replace(/\s+/g, " ").trim();
}

/**
 * Map one registry row, preserving the two absences this surface can hit: a row
 * with no description, and a read that reported no handler either way.
 */
export function toPaletteCommand(raw: unknown): PaletteCommand | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const command = String(row.command ?? "").trim();
  if (!command) return null;
  const hasHandler = row.has_handler;
  return {
    command,
    category: String(row.category ?? "general"),
    description: String(row.description ?? ""),
    usage: typeof row.usage === "string" ? row.usage : command,
    hasHandler: typeof hasHandler === "boolean" ? hasHandler : null,
  };
}

/**
 * Whether `command` is reachable by completing `prefix`.
 *
 * The rule is asymmetric on purpose, and both halves are load-bearing:
 *
 * - Every token of `prefix` **except the last** must match a whole token of
 *   `command`. Without this, `/task` also offered `/tasks` — a different
 *   command that the catalog really does contain — so the family row and its
 *   own sibling would be indistinguishable in the list.
 * - The **last** token is the one being typed, so it may be a partial prefix.
 *   A strict rule here is what made `/agent as` match nothing, hiding
 *   `/agent ask` — reintroducing the very inaccessibility this module exists to
 *   remove, one token later.
 *
 * A bare `/` is the one prefix that means "everything".
 */
function completes(command: string, prefix: string): boolean {
  if (prefix === "/") return true;
  if (command === prefix) return true;

  const lastSpace = prefix.lastIndexOf(" ");
  if (lastSpace === -1) {
    // One token typed: it must be a complete command or a family name.
    return command.startsWith(`${prefix} `);
  }

  // Multi-token draft: the head must be a whole token, the tail may be partial.
  const head = prefix.slice(0, lastSpace);
  if (!(command === head || command.startsWith(`${head} `))) return false;
  const tail = prefix.slice(lastSpace + 1);
  return command.slice(head.length + 1).startsWith(tail);
}

/**
 * Exact family row first, then alphabetical — deterministic and independent of
 * the registry's own ordering, so the palette cannot reshuffle between two
 * identical reads.
 */
function byPickOrder(a: PaletteCommand, b: PaletteCommand, prefix: string): number {
  const aExact = a.command === prefix;
  const bExact = b.command === prefix;
  if (aExact !== bExact) return aExact ? -1 : 1;
  const al = a.command.toLowerCase();
  const bl = b.command.toLowerCase();
  if (al !== bl) return al < bl ? -1 : 1;
  return a.command < b.command ? -1 : a.command > b.command ? 1 : 0;
}

/**
 * Every command reachable by completing `draft`.
 *
 * Returns `applicable: false` for a draft that is not a `/` token at all, which
 * is distinct from "applicable and nothing matched": the first means the palette
 * was never open, the second means the operator finished typing arguments
 * (`/goal create my objective`) or typed a prefix that does not exist. Both
 * render closed, but they are different facts and only the first is a decision
 * this palette makes.
 */
export function buildSlashCommandPalette(
  commands: readonly unknown[],
  draft: string,
): SlashCommandPalette {
  const prefix = normaliseCommandDraft(draft);
  const rows: PaletteCommand[] = [];
  const seen = new Set<string>();

  for (const raw of commands ?? []) {
    const row = toPaletteCommand(raw);
    if (!row || seen.has(row.command)) continue;
    seen.add(row.command);
    rows.push(row);
  }

  const total = rows.length;
  if (!prefix.startsWith("/")) {
    return { rows: [], applicable: false, prefix, matched: 0, total };
  }

  const matchedRows = rows
    .filter((row) => completes(row.command, prefix))
    .sort((a, b) => byPickOrder(a, b, prefix));

  return {
    rows: matchedRows,
    applicable: true,
    prefix,
    matched: matchedRows.length,
    total,
  };
}

/**
 * The sentence under the list.
 *
 * Two facts the row list cannot carry by itself: that the registry is the
 * authority for this count (not a hand-typed constant), and that nothing was
 * dropped. A cap that hid rows would have to say so here rather than render as
 * a complete catalog.
 */
export function describePalette(palette: SlashCommandPalette): string {
  if (!palette.applicable) return "";
  if (palette.total === 0) return "No commands were returned by the registry.";
  if (palette.matched === 0) {
    return `No command starts with ${palette.prefix} — type fewer characters, or Esc to close.`;
  }
  if (palette.matched === 1) return `1 of ${palette.total} commands matches ${palette.prefix}.`;
  return `${palette.matched} of ${palette.total} commands match ${palette.prefix}.`;
}