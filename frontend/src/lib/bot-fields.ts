/**
 * The editable field schema for a bot profile.
 *
 * WHY THIS EXISTS. `BotUpdateRequest` in
 * `backend/app/gateway/routers/bots.py` is the authority on what a bot profile
 * accepts. Hand-writing the same eighteen fields into a form means the form is a
 * copy that can drift: a field added server-side never appears, a renamed field
 * 404s on save, and a tightened validator is invisible until it rejects a save
 * the user believed was valid.
 *
 * So the field list is derived, not restated. The constraints below are
 * transcribed from the Pydantic model - `max_length`, `ge`/`le`,
 * `max_items` - and `assertAgainstServerSchema()` checks them against the
 * gateway's live OpenAPI document when one is reachable, so a divergence fails a
 * test instead of surfacing as a rejected save.
 *
 * Nothing here decides what a field MEANS. The panel renders measurements and
 * operator-asserted values differently, and `assertAgainstServerSchema` exists
 * because that split is a judgement while the field list is a fact.
 */

/** How a value is entered. Derived from the server's type, never guessed. */
export type BotFieldKind = "text" | "longtext" | "number" | "stringlist" | "objectlist";

export interface BotFieldSpec {
  /** The wire name. This is the key sent in the PATCH body. */
  readonly key: string;
  readonly label: string;
  readonly kind: BotFieldKind;
  /** Server-side cap. `undefined` means the server declared none. */
  readonly maxLength?: number;
  /** Inclusive bounds for a `number` field, from the server's `ge`/`le`. */
  readonly min?: number;
  readonly max?: number;
  /** Server-side cap on a list field's length, from `max_length` on the list. */
  readonly maxItems?: number;
  /** Longer fields, folded away behind a disclosure. */
  readonly collapsed?: boolean;
  /**
   * True for values the runtime measures rather than a human authoring them:
   * reputation, run statistics, liveness. They stay editable, but once changed
   * the panel stops describing them as measurements - see `isOperatorSet`.
   */
  readonly measured?: boolean;
  /** One line explaining what the server does with this field. */
  readonly hint: string;
}

export const BOT_FIELDS: readonly BotFieldSpec[] = [
  {
    key: "display_name",
    label: "Display name",
    kind: "text",
    maxLength: 100,
    hint: "How the bot is named in the interface.",
  },
  {
    key: "role",
    label: "Role",
    kind: "text",
    maxLength: 200,
    hint: "Its function in one line.",
  },
  {
    key: "avatar",
    label: "Avatar",
    kind: "text",
    maxLength: 16,
    hint: "A short glyph or emoji. The server caps this at 16 characters.",
  },
  {
    key: "department",
    label: "Department",
    kind: "text",
    maxLength: 64,
    hint: "The group this bot belongs to.",
  },
  {
    key: "reports_to",
    label: "Reports to",
    kind: "text",
    maxLength: 64,
    collapsed: true,
    hint: "The bot this one escalates to.",
  },
  {
    key: "succession_fallback",
    label: "Succession fallback",
    kind: "text",
    maxLength: 64,
    collapsed: true,
    hint: "Who takes over if this bot is lost.",
  },
  {
    key: "soul",
    label: "Persona / instructions",
    kind: "longtext",
    maxLength: 20000,
    hint: "The bot's standing instructions. This is what it acts on.",
  },
  {
    key: "model",
    label: "Model",
    kind: "text",
    maxLength: 200,
    hint: "Empty means no model is assigned. That is a real state, not a blank.",
  },
  {
    key: "capabilities",
    label: "Capabilities",
    kind: "stringlist",
    hint: "What this bot claims it can do. Add and remove rows.",
  },
  {
    key: "responsibilities",
    label: "Responsibilities",
    kind: "stringlist",
    hint: "What it is accountable for.",
  },
  {
    key: "toolsets",
    label: "Toolsets",
    kind: "stringlist",
    maxItems: 50,
    hint: "Declared tool groups. A wildcard such as 'all' is a sentinel, not a list of tools.",
  },
  {
    key: "skills",
    label: "Skills",
    kind: "stringlist",
    maxItems: 100,
    hint: "Installed skills this bot may use.",
  },
  {
    key: "routines",
    label: "Routines",
    kind: "objectlist",
    hint: "Scheduled or recurring jobs attached to this bot.",
  },
  {
    key: "status",
    label: "Status",
    kind: "text",
    maxLength: 16,
    hint: "Live state. Pausing through the pause action records a reason; typing it here does not.",
  },
  {
    key: "reputation_score",
    label: "Reputation",
    kind: "number",
    min: 0,
    max: 1,
    measured: true,
    hint: "Normally derived from recorded runs. Editing it makes this an operator assertion.",
  },
  {
    key: "task_stats",
    label: "Run statistics",
    kind: "objectlist",
    measured: true,
    hint: "Normally written by the runtime. Editing it rewrites what is claimed to have happened.",
  },
  {
    key: "heartbeat",
    label: "Heartbeat",
    kind: "text",
    maxLength: 32,
    measured: true,
    collapsed: true,
    hint: "Liveness, written by the bot itself. Editing it forges a live signal.",
  },
  {
    key: "last_active",
    label: "Last active",
    kind: "text",
    maxLength: 32,
    measured: true,
    collapsed: true,
    hint: "A recorded timestamp, not a setting.",
  },
] as const;

/** Fields rendered in the main body; the rest sit behind a disclosure. */
export function visibleBotFields(): BotFieldSpec[] {
  return BOT_FIELDS.filter((f) => !f.collapsed);
}

export function collapsedBotFields(): BotFieldSpec[] {
  return BOT_FIELDS.filter((f) => f.collapsed);
}

export function botFieldSpec(key: string): BotFieldSpec | undefined {
  return BOT_FIELDS.find((f) => f.key === key);
}

/** Only the keys whose value actually differs from the server's. */
export function changedBotFields(
  original: Record<string, unknown>,
  draft: Record<string, unknown>,
): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  for (const field of BOT_FIELDS) {
    if (!(field.key in draft)) continue;
    if (stableEqual(original[field.key], draft[field.key])) continue;
    patch[field.key] = draft[field.key];
  }
  return patch;
}

/** True when there is anything to send. An empty PATCH body is a 422. */
export function hasChanges(patch: Record<string, unknown>): boolean {
  return Object.keys(patch).length > 0;
}

/**
 * A value the operator has overwritten is no longer a measurement.
 *
 * The panel cannot keep showing a reputation score as "measured" once someone
 * has typed a different one; the number would be described two ways at once and
 * one of them would be false. `null` and absent are both "the server did not
 * report this", so a field the operator never touched stays a measurement even
 * when it is empty.
 */
export function isOperatorSet(
  field: BotFieldSpec,
  original: Record<string, unknown>,
  draft: Record<string, unknown>,
): boolean {
  if (!field.measured) return false;
  if (!(field.key in draft)) return false;
  return !stableEqual(original[field.key], draft[field.key]);
}

function stableEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || a === undefined || b === null || b === undefined) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((item, i) => stableEqual(item, b[i]));
  }
  if (typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a as object).sort();
    const kb = Object.keys(b as object).sort();
    if (ka.length !== kb.length) return false;
    if (ka.some((k, i) => k !== kb[i])) return false;
    return ka.every((k) =>
      stableEqual(
        (a as Record<string, unknown>)[k],
        (b as Record<string, unknown>)[k],
      ),
    );
  }
  return false;
}

/**
 * Compare the transcribed constraints against the gateway's live schema.
 *
 * Returns the list of divergences rather than throwing, so a caller can report
 * every one at once. An unreachable or doc-disabled Gateway is reported as
 * "unknown", never as agreement - a check that cannot run must not read as a
 * check that passed.
 */
export function assertAgainstServerSchema(
  schema: unknown,
): { checked: number; divergences: string[]; status: "compared" | "unavailable" } {
  if (!schema || typeof schema !== "object") {
    return { checked: 0, divergences: [], status: "unavailable" };
  }
  const openapi = schema as Record<string, any>;
  const body = openapi?.components?.schemas?.BotUpdateRequest;
  if (!body?.properties) {
    return { checked: 0, divergences: [], status: "unavailable" };
  }

  const serverKeys = Object.keys(body.properties as Record<string, unknown>);
  const ours = BOT_FIELDS.map((f) => f.key).sort();
  const theirs = [...serverKeys].sort();
  const divergences: string[] = [];

  for (const key of ours) {
    if (!theirs.includes(key)) {
      divergences.push(`${key}: editable here but absent from the server's BotUpdateRequest`);
    }
  }
  for (const key of theirs) {
    if (!ours.includes(key)) {
      divergences.push(`${key}: accepted by the server but not editable here`);
    }
  }

  let checked = 0;
  for (const field of BOT_FIELDS) {
    const prop = (body.properties as Record<string, any>)[field.key];
    if (!prop) continue;
    checked += 1;
    const serverMax = prop.maxLength ?? prop.maxItems ?? undefined;
    const declared = field.maxLength ?? field.maxItems;
    if (serverMax !== undefined && declared !== undefined && serverMax !== declared) {
      divergences.push(
        `${field.key}: this form allows ${declared}, the server allows ${serverMax}`,
      );
    }
    if (field.kind === "number") {
      if (prop.minimum !== undefined && prop.minimum !== field.min) {
        divergences.push(`${field.key}: minimum ${String(prop.minimum)} here vs ${String(field.min)}`);
      }
      if (prop.maximum !== undefined && prop.maximum !== field.max) {
        divergences.push(`${field.key}: maximum ${String(prop.maximum)} here vs ${String(field.max)}`);
      }
    }
  }

  return { checked, divergences, status: "compared" };
}
