/**
 * Per-bot run counters, exactly as `GET /api/bots` reports them.
 *
 * The Gateway sends `total_runs` / `completed` / `failed` / `avg_duration_sec`
 * (verified against a live response). This type once declared `total` and
 * `succeeded`, which the server never sends, so every read of those names was
 * `undefined` and every `Number(undefined) || 0` fabricated a zero: the fleet
 * bar read "0 Tasks done" and every card read "0 tasks" no matter how much work
 * had actually run.
 *
 * Every field is optional and measured-on-demand: an absent counter is "the
 * server did not report it", never zero. Use the `totalRuns` / `completedRuns`
 * / `failedRuns` helpers in `lib/bots.ts` rather than indexing this directly.
 */
export interface BotTaskStats {
  total_runs?: number | null;
  completed?: number | null;
  failed?: number | null;
  avg_duration_sec?: number | null;
  [key: string]: unknown;
}

export interface BotProfile {
  name: string;
  display_name: string;
  role: string;
  soul?: string;
  model?: string;
  toolsets: string[];
  skills: string[];
  avatar: string;
  status: "active" | "paused" | "disabled" | string;
  last_active?: string | null;
  version: number;
  epoch?: string | null;
  department: string;
  reports_to?: string | null;
  responsibilities: string[];
  capabilities: string[];
  heartbeat?: string | null;
  succession_fallback?: string | null;
  /**
   * Measured reputation (0–1), or null when the bot has no recorded runs.
   * Null means "unverified" — never coerce it to 0 or 1 at render time.
   */
  reputation_score: number | null;
  task_stats: BotTaskStats;
  routines: Array<Record<string, unknown>>;
  created_at?: string | null;
  updated_at?: string | null;
  /**
   * Roster activity, present only when the client asked for it
   * (`GET /api/bots?activity=true`) and `null` otherwise — an unrequested
   * projection is absent, not an empty roster row.
   *
   * `last_message_withheld` means the newest body looked credential-shaped and
   * the server deliberately did not project it; `last_message_preview` is then
   * null even though a message exists. `last_message_at` is epoch **seconds**,
   * which `lib/time.ts` normalizes.
   */
  unread_count?: number | null;
  last_message_preview?: string | null;
  last_message_at?: number | null;
  last_message_sender?: string | null;
  last_message_withheld?: boolean | null;
}

export interface BotTemplate {
  name: string;
  display: string;
  role: string;
  avatar: string;
  department: string;
  reports_to?: string | null;
  responsibilities: string[];
  capabilities: string[];
}

export interface FleetHealth {
  total: number;
  active: number;
  paused: number;
  disabled: number;
  /**
   * Average of MEASURED reputation scores only (nulls excluded), or null
   * when no bot has a measured score — never a fabricated fleet number.
   */
  avg_reputation: number | null;
  /**
   * Sum of the bots' MEASURED `task_stats.total_runs`, or null when no bot
   * reported a counter at all. Never a fabricated 0: "0 tasks done" claims the
   * server measured no work, which is a different fact from "no counter was
   * reported".
   */
  total_tasks: number | null;
}

export function botDisplayName(bot: BotProfile): string {
  return bot.display_name || bot.name;
}

/**
 * The name to put on a roster row, disambiguated only when it has to be.
 *
 * THE DEFECT THIS FIXES. `display_name` is not a unique identifier, and on a
 * real deployment it is very far from one. Captured live from
 * `GET /api/bots` (58 bots, 0 duplicate `name`s, **8 colliding display names
 * covering 35 of the 58 cards**):
 *
 *   "Data Engineer"                 x8   (bot_ed5fc1, bot_f62dd2, ... bot_cb596e)
 *   "Solidity_Security Specialist"  x7
 *   "Cuda_Kernel_Opt Specialist"    x7
 *   "Researcher" / "Coder" / "Tester" x3 each
 *   "Architect" / "Support"         x2 each
 *
 * Every card therefore rendered the same headline, and `bot.name` - the one
 * field that actually separates them - appeared nowhere in the grid. Two bots
 * with different names, different departments and different capability chips
 * were indistinguishable in the roster, so "which Data Engineer?" had no answer
 * on screen.
 *
 * The rule is deliberately narrow, because over-disambiguating is its own lie:
 * the qualifier is added ONLY for a bot whose label actually collides in the
 * given roster. A unique name is left exactly as the server sent it, and an
 * absent roster (`null`) means "collision unknown", which must not be read as
 * "this one is unique" - so it degrades to the plain display name rather than
 * inventing a distinction.
 */
export function botRosterLabel(bot: BotProfile, colliding: ReadonlySet<string> | null): string {
  const label = botDisplayName(bot);
  if (!colliding || !colliding.has(label)) return label;
  const id = (bot.name ?? "").trim();
  // A colliding label with no id to fall back on stays as the server sent it;
  // appending nothing is better than appending an empty qualifier.
  if (!id || id === label) return label;
  // Case-only difference is not a distinction. The live roster contains
  // `architect` alongside `cto`, both displayed as "Architect" - and the
  // qualifier "(architect)" would have added two inches of noise beside
  // "(cto)" while telling the operator nothing they could not already infer
  // from the chip. The id has to actually differ to earn the qualifier.
  if (id.toLowerCase() === label.toLowerCase()) return label;
  return `${label} (${id})`;
}

/** The set of display labels that appear more than once across `bots`. */
export function collidingBotLabels(bots: readonly BotProfile[]): Set<string> {
  const seen = new Map<string, number>();
  for (const b of bots) {
    const label = botDisplayName(b);
    seen.set(label, (seen.get(label) ?? 0) + 1);
  }
  const dupes = new Set<string>();
  for (const [label, n] of seen) if (n > 1) dupes.add(label);
  return dupes;
}

export function botInitials(bot: BotProfile): string {
  const label = botDisplayName(bot).trim();
  if (!label) return "?";
  const parts = label.split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}
