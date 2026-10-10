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
 *
 * **New in v2: Command Families & Subcommand Expansion**
 * - Commands are organized into families (e.g., `/goal` + `/goal create` + `/goal start`)
 * - Parent commands can be expanded to show subcommands
 * - Right-arrow / Enter on a parent expands its subcommands
 * - Left-arrow / Escape collapses back to parent level
 * - Bare `/` shows all top-level command families
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
 * Split palette rows for the "runnable only" toggle.
 *
 * Only `hasHandler === false` rows are hidden — the server positively reported
 * no bound handler for those. `null` (the read did not say) stays visible:
 * unknown is not negative, and hiding it would present an unverified subset as
 * "all runnable". Returns both halves so the footer can name what it hid.
 */
export function splitRunnableRows(rows: readonly PaletteCommand[]): {
  visible: PaletteCommand[];
  hidden: number;
} {
  // A malformed list degrades to empty rather than throwing mid-render: the
  // palette already handles "no rows" as a closed state.
  const list = Array.isArray(rows) ? rows : [];
  const visible = list.filter((r) => r && r.hasHandler !== false);
  return { visible, hidden: list.length - visible.length };
}

/**
 * Headline counts for "N runnable of M listed".
 *
 * `runnable` counts only `hasHandler === true`; everything else — `false` and
 * `null` alike — is not claimed runnable. When the registry read failed the
 * caller must not render counts at all (the list is the built-in fallback, and
 * "0 runnable" would claim a health nobody measured).
 */
export function runnableHeadline(rows: ReadonlyArray<{ hasHandler?: boolean | null }>): {
  runnable: number;
  listed: number;
} {
  const list = Array.isArray(rows) ? rows : [];
  return {
    runnable: list.filter((r) => r && r.hasHandler === true).length,
    listed: list.length,
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
export function completes(command: string, prefix: string): boolean {
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
export function byPickOrder(a: PaletteCommand, b: PaletteCommand, prefix: string): number {
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

/**
 * =============================================================================
 * COMMAND FAMILIES & SUBCOMMAND EXPANSION (v2)
 * =============================================================================
 *
 * Commands are organized into families (e.g., `/goal` + `/goal create` + `/goal start`).
 * Parent commands can be expanded to show subcommands.
 * Right-arrow / Enter on a parent expands its subcommands.
 * Left-arrow / Escape collapses back to parent level.
 * Bare `/` shows all top-level command families.
 */

/** Extended command row with family metadata */
export interface FamilyCommand extends PaletteCommand {
  /** Whether this command has subcommands (is a family parent) */
  isFamily: boolean;
  /** The parent command path (e.g., "/goal" for "/goal create") */
  parentPath: string | null;
  /** Depth in the command hierarchy (0 = top-level) */
  depth: number;
  /** Unique family ID for grouping */
  familyId: string;
}

/** State for expanded command families */
export interface FamilyExpansionState {
  /** Set of expanded family IDs */
  expandedFamilies: Set<string>;
  /** Current navigation path (for breadcrumb) */
  navigationPath: string[];
}

/**
 * Extract the parent path from a command.
 * e.g., "/goal create" -> "/goal", "/agent spawn researcher" -> "/agent"
 */
export function getParentPath(command: string): string | null {
  const parts = command.split(" ");
  if (parts.length <= 1) return null;
  return parts[0];
}

/**
 * Get the family ID for a command.
 * All commands sharing the same first token belong to the same family.
 * e.g., "/goal", "/goal create", "/goal start" all have familyId "/goal"
 */
export function getFamilyId(command: string): string {
  return command.split(" ")[0];
}

/**
 * Check if a command is a family parent (has subcommands).
 */
export function isFamilyParent(command: string, allCommands: readonly PaletteCommand[]): boolean {
  const familyId = getFamilyId(command);
  return allCommands.some(
    (cmd) => getFamilyId(cmd.command) === familyId && cmd.command !== command
  );
}

/**
 * Get all subcommands for a family parent.
 */
export function getSubcommands(parentCommand: string, allCommands: readonly PaletteCommand[]): PaletteCommand[] {
  const familyId = getFamilyId(parentCommand);
  return allCommands
    .filter((cmd) => getFamilyId(cmd.command) === familyId && cmd.command !== parentCommand)
    .sort((a, b) => a.command.localeCompare(b.command));
}

/**
 * Build the command hierarchy from flat command list.
 * Returns commands enriched with family metadata.
 */
export function buildCommandHierarchy(commands: readonly PaletteCommand[]): FamilyCommand[] {
  return commands.map((cmd) => {
    const familyId = getFamilyId(cmd.command);
    const parentPath = getParentPath(cmd.command);
    const depth = parentPath ? parentPath.split(" ").length : 0;
    const isFamily = isFamilyParent(cmd.command, commands);
    
    return {
      ...cmd,
      isFamily,
      parentPath,
      depth,
      familyId,
    };
  });
}

/**
 * Filter commands based on prefix, respecting family expansion state.
 * When a family is expanded, show its subcommands at depth+1.
 */
export function filterCommandsWithFamilies(
  allCommands: readonly FamilyCommand[],
  prefix: string,
  expandedFamilies: Set<string>,
  navigationPath: string[]
): FamilyCommand[] {
  if (!prefix.startsWith("/")) return [];
  
  // If we're navigating within a family (e.g., "/goal "), show subcommands
  if (navigationPath.length > 0) {
    const currentFamily = navigationPath[navigationPath.length - 1];
    const subcommands = allCommands.filter(
      (cmd) => cmd.parentPath === currentFamily
    );
    const matched = subcommands.filter((cmd) => 
      cmd.command.startsWith(prefix.slice(1) + " ")
    );
    return matched.sort((a, b) => a.command.localeCompare(b.command));
  }
  
  // Top-level filtering: match against top-level commands only
  const topLevel = allCommands.filter((cmd) => cmd.depth === 0);
  
  return topLevel
    .filter((cmd) => {
      if (prefix === "/") return true;
      const cmdTokens = cmd.command.slice(1).split(" ");
      const prefixTokens = prefix.slice(1).split(" ").filter(Boolean);
      
      // Match against command tokens
      return cmdTokens.some((token, idx) => 
        idx < prefixTokens.length && token.startsWith(prefixTokens[idx])
      );
    })
    .sort((a, b) => {
      // Exact match first, then prefix matches, then alphabetical
      const aExact = a.command === prefix.slice(1);
      const bExact = b.command === prefix.slice(1);
      if (aExact !== bExact) return aExact ? -1 : 1;
      return a.command.localeCompare(b.command);
    });
}

/**
 * Toggle family expansion.
 */
export function toggleFamilyExpansion(
  expandedFamilies: Set<string>,
  familyId: string
): Set<string> {
  const newSet = new Set(expandedFamilies);
  if (newSet.has(familyId)) {
    newSet.delete(familyId);
  } else {
    newSet.add(familyId);
  }
  return newSet;
}

/**
 * Navigate into a family (for subcommand view).
 */
export function navigateIntoFamily(
  navigationPath: string[],
  familyId: string
): string[] {
  if (!navigationPath.includes(familyId)) {
    return [...navigationPath, familyId];
  }
  return navigationPath;
}

/**
 * Navigate back (pop navigation path).
 */
export function navigateBack(navigationPath: string[]): string[] {
  if (navigationPath.length === 0) return [];
  return navigationPath.slice(0, -1);
}

/**
 * Get the display path for the current navigation context.
 */
export function getNavigationDisplay(navigationPath: string[]): string {
  if (navigationPath.length === 0) return "All Commands";
  return navigationPath.map((p) => `/${p}`).join(" > ");
}

/**
 * Check if a command is a leaf (executable) vs a family parent.
 */
export function isLeafCommand(
  command: FamilyCommand,
  allCommands: readonly FamilyCommand[]
): boolean {
  return !command.isFamily;
}

/**
 * Get keyboard hint for a command row.
 */
export function getCommandKeyHint(command: FamilyCommand): string {
  if (command.isFamily) {
    return "→ expand";
  }
  return "Enter";
}