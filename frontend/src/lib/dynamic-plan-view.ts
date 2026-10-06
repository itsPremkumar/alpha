/**
 * What a dynamic-workflow preview must say about its own resources.
 *
 * ## The defect
 *
 * `WorkflowsSection.tsx` renders:
 *
 *     Tools: {resources.tools.join(", ") || "alpha.tools"}
 *
 * and reads `resources.metadata` **nowhere**. Measured against the live Gateway
 * on 2026-10-06 with `backend/scripts/probe_dynamic_tool_arithmetic.py`, three
 * prompts:
 *
 * | prompt            | tools selected | `unavailable_tools` | actually available |
 * | ----------------- | -------------- | ------------------- | ------------------ |
 * | narrow-refactor   | 12             | 9                   | 3                  |
 * | research          | 17             | 14                  | 3                  |
 * | boost-build       | 12             | 9                   | 3                  |
 *
 * So the panel printed a 12-to-17 entry tool list of which **at most three**
 * could resolve, with no qualifier. A reader takes that list as "these will be
 * used". That is the same absent-as-present defect as rendering an unreported
 * count as zero, pointing the other way: a list of names is not a list of
 * capabilities.
 *
 * Two more claims the payload contradicts and the panel repeated:
 *
 * - The heading reads **"Assembled Bot Specialists & Tools"** while the same
 *   payload carries `metadata.provisioned: false`.
 * - `metadata.skill_generation` is a real server sentence —
 *   `"not requested"` on one prompt, `"unavailable — no generator bound"` on
 *   another. Both were discarded. They are different facts and only one of them
 *   belongs on screen for a given run.
 *
 * ## The rules, each with a tempting wrong reading
 *
 * | Payload | This renders | Never |
 * | --- | --- | --- |
 * | no `metadata` at all | `tool availability not reported` | `0 unavailable` |
 * | `unavailable_tools: []` | `all N selected tools are available` | `not reported` |
 * | `unavailable_tools: [...]` | `N of M selected tools are unavailable`, and which | the bare list |
 * | `provisioned: false` | `not provisioned on this Gateway` | `Assembled` |
 * | no `provisioned` key | `provisioning state not reported` | `provisioned` |
 * | `skill_generation: "not requested"` | the sentence verbatim | `no skills found` |
 * | `total_skills: 0` + `"not requested"` | `0 requested` | `0 skills exist` |
 *
 * That last row is the subtle one. `total_skills: 0` is a fact about what this
 * plan asked for, never a fact about the installation's skill catalogue, and
 * collapsing the two would tell an operator their skills are missing.
 *
 * Lives in its own module, like `teamops-progress.ts` and
 * `swarm-structure-view.ts`, so the rules are testable without loading the
 * 2400-line section's import graph.
 */

/** A finite non-negative count, or `null` for "not reported". `0` is preserved. */
export function measuredCount(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return value;
}

function objectOr(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringOr(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function stringListOrNull(value: unknown): string[] | null {
  // `null` means the field was absent; `[]` means the server measured zero.
  if (!Array.isArray(value)) return null;
  return value.filter((v): v is string => typeof v === "string" && v.length > 0);
}

/** The resource-availability reading for one plan. */
export interface PlanToolAvailability {
  /** Selected tool names, in the server's order. */
  selected: string[];
  /** Selected names the payload ALSO lists as unavailable. */
  blocked: string[];
  /** Selected minus blocked, in the server's order. */
  available: string[];
  /** `null` when the payload carried no availability data at all. */
  reported: boolean;
  /** What to print instead of a bare tool list. */
  sentence: string;
}

export function planToolAvailability(resources: unknown): PlanToolAvailability {
  const res = objectOr(resources);
  const selected = stringListOrNull(res.tools) ?? [];

  // `metadata` absent and `metadata.unavailable_tools` absent are the same
  // reading here: nothing measured availability, so nothing may be claimed.
  const metadata = objectOr(res.metadata);
  const unavailableRaw = stringListOrNull(metadata.unavailable_tools);
  const reported = unavailableRaw !== null;
  const unavailable = unavailableRaw ?? [];
  const blockedSet = new Set(unavailable);

  const blocked = selected.filter((t) => blockedSet.has(t));
  const available = selected.filter((t) => !blockedSet.has(t));

  let sentence: string;
  if (!reported) {
    sentence =
      selected.length > 0
        ? `${selected.length} tool(s) selected; tool availability not reported by the server`
        : "no tools selected, and tool availability was not reported by the server";
  } else if (selected.length === 0) {
    sentence = "no tools were selected for this plan";
  } else if (blocked.length === 0) {
    sentence = `all ${selected.length} selected tool(s) are available`;
  } else {
    sentence = `${blocked.length} of ${selected.length} selected tool(s) are unavailable`;
  }

  return { selected, blocked, available, reported, sentence };
}

/** The provisioning reading. `null` is "not reported", never "false". */
export interface PlanProvisioning {
  provisioned: boolean | null;
  sentence: string;
  totalBots: number | null;
  /** The server's own words, verbatim, or `null` when it sent none. */
  skillGeneration: string | null;
  mcpGeneration: string | null;
  totalSkills: number | null;
  /** A sentence about skills that never turns a request into a catalogue fact. */
  skillSentence: string;
  mcpSentence: string;
}

export function planProvisioning(resources: unknown): PlanProvisioning {
  const res = objectOr(resources);
  const metadata = objectOr(res.metadata);
  const hasMetadata = Object.keys(metadata).length > 0;

  const provisioned = typeof metadata.provisioned === "boolean" ? metadata.provisioned : null;
  const totalBots = measuredCount(metadata.total_bots);
  const totalSkills = measuredCount(metadata.total_skills);
  const skillGeneration = stringOr(metadata.skill_generation);
  const mcpGeneration = stringOr(metadata.mcp_generation);

  let sentence: string;
  if (!hasMetadata) {
    sentence = "provisioning state not reported by the server";
  } else if (provisioned === null) {
    sentence = "provisioning state not reported by the server";
  } else if (provisioned) {
    sentence = "specialists were provisioned for this plan";
  } else {
    // The default on this Gateway. It is a real limitation and it is stated as
    // one: `provisioned: false` means the named bots are a plan, not a roster.
    sentence = "not provisioned — the named specialists are a plan on this Gateway, not a live roster";
  }

  // `total_skills: 0` is a fact about what this plan REQUESTED. The server's own
  // generation sentence says which of "not requested" / "no generator bound" /
  // "generated N" applies, so the count is never presented alone.
  let skillSentence: string;
  if (skillGeneration) {
    skillSentence = skillGeneration;
  } else if (totalSkills === null) {
    skillSentence = "skill selection not reported by the server";
  } else if (totalSkills === 0) {
    skillSentence = "0 skills selected for this plan; the server sent no reason";
  } else {
    skillSentence = `${totalSkills} skill(s) selected`;
  }

  const mcpSentence = mcpGeneration ?? "MCP generation not reported by the server";

  return {
    provisioned,
    sentence,
    totalBots,
    skillGeneration,
    mcpGeneration,
    totalSkills,
    skillSentence,
    mcpSentence,
  };
}

/** The whole preview's honesty layer, so the section renders without deriving. */
export interface PlanResourceView {
  tools: PlanToolAvailability;
  provisioning: PlanProvisioning;
  /** What the "Assembled" heading may claim. */
  heading: string;
}

export function planResourceView(resources: unknown): PlanResourceView {
  const provisioning = planProvisioning(resources);
  return {
    tools: planToolAvailability(resources),
    provisioning,
    // The heading followed the payload rather than the payload following the
    // heading. "Assembled" is a claim about provisioning, so it is only made
    // when the server said provisioning happened.
    heading: provisioning.provisioned
      ? "Assembled Bot Specialists & Tools"
      : "Planned Bot Specialists & Tools",
  };
}