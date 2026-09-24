// lib/moa.ts — real-API client for the Mixture-of-Agents (MoA) plane.
//
// Router: backend/app/gateway/routers/models.py
//   GET  /api/models/moa      → MoaStatusResponse (engine/command/limits/orchestrator/
//                                redaction/tool + per-field error disclosures)
//   POST /api/models/moa/run  → MoaRunResponse (candidates + consensus + evidence_kind)
//
// The honesty anchor of this plane is `evidence_kind`:
//   "real"      — the production model client generated the candidates in this request
//   "failed"    — the production client ran but every candidate failed (no output exists)
//   "simulated" — the model client was a stub/injection; the text is NOT model output
// The UI must render the kind and its note verbatim; it never re-labels a run.
import { get, send, pick } from "./http";

export interface MoaStatus {
  capability_id: string;
  /** Capability-registry entry, or null when the registry could not be read. */
  engine: Record<string, unknown> | null;
  engine_error: string | null;
  /** /moa command catalog row + handler availability, or null. */
  command: Record<string, unknown> | null;
  command_error: string | null;
  limits: Record<string, unknown>;
  limits_error: string | null;
  orchestrator: Record<string, unknown>;
  redaction: Record<string, unknown>;
  tool: Record<string, unknown>;
}

function errOrNull(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function objOrNull(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : null;
}

/** GET /api/models/moa — live MoA subsystem status. */
export async function getMoaStatus(): Promise<MoaStatus> {
  const d = await get<Record<string, unknown>>("/models/moa");
  return {
    capability_id: String(pick(d, ["capability_id"], "moa_engine")),
    engine: objOrNull(d.engine),
    engine_error: errOrNull(d.engine_error),
    command: objOrNull(d.command),
    command_error: errOrNull(d.command_error),
    limits: objOrNull(d.limits) ?? {},
    limits_error: errOrNull(d.limits_error),
    orchestrator: objOrNull(d.orchestrator) ?? {},
    redaction: objOrNull(d.redaction) ?? {},
    tool: objOrNull(d.tool) ?? {},
  };
}

export interface MoaCandidate {
  model_name: string;
  success: boolean;
  response: string;
  error: string | null;
  duration_ms: number;
}

export type MoaEvidenceKind = "real" | "simulated" | "failed" | string;

export interface MoaRunResult {
  /** The engine-normalized (redacted) prompt that was actually used. */
  prompt: string;
  consensus_response: string;
  candidates: MoaCandidate[];
  candidate_models: string[];
  total_duration_ms: number;
  evidence_kind: MoaEvidenceKind;
  evidence_note: string;
}

/**
 * POST /api/models/moa/run — run one real MoA round.
 * The caller supplies the candidate model names; the server caps them at the
 * engine's MAX_ADVISORS, re-checks `model:use` authorization for every one,
 * redacts the prompt, and discloses `evidence_kind` honestly.
 */
export async function runMoaRound(body: { prompt: string; candidate_models: string[] }): Promise<MoaRunResult> {
  const d = await send<Record<string, unknown>>("/models/moa/run", "POST", {
    prompt: body.prompt,
    candidate_models: body.candidate_models,
  });
  const total = Number(pick(d, ["total_duration_ms"], 0));
  const candidates = Array.isArray(d.candidates) ? (d.candidates as Array<Record<string, unknown>>) : [];
  return {
    prompt: String(pick(d, ["prompt"], "")),
    consensus_response: String(pick(d, ["consensus_response"], "")),
    candidates: candidates.map((c) => {
      const duration = Number(pick(c, ["duration_ms"], 0));
      return {
        model_name: String(pick(c, ["model_name"], "")),
        success: c.success === true,
        response: typeof c.response === "string" ? c.response : "",
        error: errOrNull(c.error),
        duration_ms: Number.isFinite(duration) ? duration : 0,
      };
    }),
    candidate_models: Array.isArray(d.candidate_models) ? d.candidate_models.map((m) => String(m)) : [],
    total_duration_ms: Number.isFinite(total) ? total : 0,
    evidence_kind: String(pick(d, ["evidence_kind"], "unknown")),
    evidence_note: String(pick(d, ["evidence_note"], "")),
  };
}
