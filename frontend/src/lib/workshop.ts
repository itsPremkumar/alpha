import { get, send, asList, pick } from "./http";

// Skill Synthesis Workshop client — real gateway surface only.
// Router: backend/app/gateway/routers/skills_workshop.py (prefix /api/skills/workshop).

/** Evidence reference record as normalized by _normalize_evidence ({"ref": ...} plus caller keys). */
export interface WorkshopEvidence {
  ref: string;
  [key: string]: unknown;
}

/** Offline evaluation record returned by SkillEvolutionEngine.evaluate (evaluation dict). */
export interface WorkshopEvaluation {
  ran_at?: string;
  error?: string | null;
  /** "structural-only" | "structural+subprocess-suite" | null — the validation ACTUALLY performed. */
  validation_kind?: string | null;
  checks_total?: number;
  checks_passed?: number;
  score?: number | null;
  findings?: string[];
  suite?: Record<string, unknown> | null;
  [key: string]: unknown;
}

/** Moderation result recorded alongside an evaluation (status/model/reason). */
export interface WorkshopModeration {
  status?: string;
  model?: string | null;
  reason?: string;
  [key: string]: unknown;
}

/** SkillEvolutionProposal.to_dict() — the honest proposal record. */
export interface SkillEvolutionProposal {
  id: string;
  skill_name: string;
  candidate_version: string;
  candidate_md?: string;
  base_version?: string | null;
  status: string;
  evidence: WorkshopEvidence[];
  evaluation?: WorkshopEvaluation | null;
  moderation?: WorkshopModeration | null;
  notes?: string[];
  reject_reason?: string | null;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
  history?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

function toProposal(raw: Record<string, unknown>): SkillEvolutionProposal {
  const evaluation = raw.evaluation && typeof raw.evaluation === "object"
    ? (raw.evaluation as WorkshopEvaluation)
    : null;
  const moderation = raw.moderation && typeof raw.moderation === "object"
    ? (raw.moderation as WorkshopModeration)
    : null;
  return {
    ...raw,
    id: String(pick(raw, ["id", "proposal_id"], "")),
    skill_name: String(pick(raw, ["skill_name", "name"], "")),
    candidate_version: String(pick(raw, ["candidate_version"], "")),
    status: String(pick(raw, ["status"], "proposed")),
    evidence: asList(raw.evidence, ["evidence"]).map((e) => ({
      ...e,
      ref: String(pick(e, ["ref"], "")),
    })),
    evaluation,
    moderation,
    notes: asList(raw.notes, ["notes"]).map((n) => String(pick(n, ["note"], n))),
    history: asList(raw.history, ["history"]),
  } as SkillEvolutionProposal;
}

/** GET /api/skills/workshop/evolution — list proposals (admin-gated by the router). */
export async function listWorkshopProposals(status?: string): Promise<SkillEvolutionProposal[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const d = await get<unknown>(`/skills/workshop/evolution${query}`);
  return asList(d, ["proposals", "data"]).map((raw) => toProposal(raw));
}

/** GET /api/skills/workshop/evolution/{proposal_id} — one proposal record. */
export async function getWorkshopProposal(proposalId: string): Promise<SkillEvolutionProposal> {
  const d = await get<Record<string, unknown>>(`/skills/workshop/evolution/${encodeURIComponent(proposalId)}`);
  return toProposal(d ?? {});
}

/** POST /api/skills/workshop/distill — SkillDraft.to_dict() on success. */
export async function distillSkillDraft(input: {
  name: string;
  description: string;
  steps: Array<Record<string, unknown>>;
  verification_command?: string;
  when_to_use?: string[];
  prerequisites?: string[];
  pitfalls?: string[];
}): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/skills/workshop/distill", "POST", input);
}

/** POST /api/skills/workshop/publish — {status, name, path} on success; 422/409 on rejection. */
export async function publishSkillDraft(input: {
  name: string;
  description: string;
  markdown_content: string;
  overwrite?: boolean;
}): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/skills/workshop/publish", "POST", input);
}

/** POST /api/skills/workshop/evolution — propose a candidate (201, active skill untouched). */
export async function proposeSkillEvolution(input: {
  skill_name: string;
  candidate_markdown: string;
  evidence: string[];
  created_by?: string;
}): Promise<SkillEvolutionProposal> {
  const d = await send<Record<string, unknown>>("/skills/workshop/evolution", "POST", input);
  return toProposal(d ?? {});
}

/** POST /api/skills/workshop/evolution/{id}/evaluate — offline validation + moderation. */
export async function evaluateSkillEvolution(proposalId: string): Promise<SkillEvolutionProposal> {
  const d = await send<Record<string, unknown>>(
    `/skills/workshop/evolution/${encodeURIComponent(proposalId)}/evaluate`,
    "POST",
    {}
  );
  return toProposal(d ?? {});
}

/** POST /api/skills/workshop/evolution/{id}/promote — explicit approve required while auto_promote is off. */
export async function promoteSkillEvolution(
  proposalId: string,
  input: { approve?: boolean; reason?: string } = {}
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(
    `/skills/workshop/evolution/${encodeURIComponent(proposalId)}/promote`,
    "POST",
    { approve: input.approve ?? false, reason: input.reason ?? "" }
  );
}

/** POST /api/skills/workshop/evolution/{id}/rollback — restore the retained pre-promotion body. */
export async function rollbackSkillEvolution(
  proposalId: string,
  reason = ""
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(
    `/skills/workshop/evolution/${encodeURIComponent(proposalId)}/rollback`,
    "POST",
    { reason }
  );
}
