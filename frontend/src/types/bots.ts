export interface BotTaskStats {
  total?: number;
  succeeded?: number;
  failed?: number;
  avg_duration_sec?: number;
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
  total_tasks: number;
}

export function botDisplayName(bot: BotProfile): string {
  return bot.display_name || bot.name;
}

export function botInitials(bot: BotProfile): string {
  const label = botDisplayName(bot).trim();
  if (!label) return "?";
  const parts = label.split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}
